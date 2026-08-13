# !/usr/bin/python3
# -*- coding: utf-8 -*-
"""
Embedded, live-updating (while the task is running) plot of wheel position vs. time for a
session, opened via right-click "Wheel Position" on a session in the project tree.

Modeled directly on pybpod-gui-plugin-trial-timeline's TrialTimeline (same ControlMatplotlib +
QTimer + MDI-panel pattern) -- that plots per-trial state durations; this plots the rotary
encoder's own position trace, threshold lines, and trial-start markers instead, reading them from
values a task script registers into the session's own data stream via
Bpod.register_value(name, value) (VAL-type rows), the same durable, already-visible-elsewhere
record used by the Board console and the session's own .csv -- no separate PNG/log file to go
hunting for.

Expects a task to call, during its own run:
    my_bpod.register_value('LEFT_THRESHOLD_DEG', VAR_LEFT_THRESHOLD_DEG)   # once, at start
    my_bpod.register_value('RIGHT_THRESHOLD_DEG', VAR_RIGHT_THRESHOLD_DEG) # once, at start
    my_bpod.register_value('TRIAL_START', elapsed_seconds)                # once per trial, when
                                                                           # Hold genuinely completes
    my_bpod.register_value('CHOICE_MADE', elapsed_seconds)                # once per trial, if a
                                                                           # real choice was detected
    my_bpod.register_value('TIMED_OUT', elapsed_seconds)                  # once per trial, if the
                                                                           # response window expired
    my_bpod.register_value('WHEEL_POS', '{elapsed_seconds},{position_deg}')  # repeatedly
(see wheel_turn_reward.py)

Not registering/plotting a marker per hold break: a jittery hold can break 100-300+ times before
settling (each refire only ~5-10ms apart -- see wheel_turn_reward.py's handle_softcode() docstring),
and neither that many rows in the session log nor that many redrawn markers is useful.

Redraws use persistent matplotlib artists (the trace Line2D, the two threshold axhlines, one axvline
per trial-boundary marker), updated/appended to in place rather than torn down and rebuilt from
scratch every timer tick (axes.clear() + full re-plot) -- the latter gets slower as a session goes on
purely from artist-creation churn, on top of read_data()'s own now-incremental row scan.
"""
import traceback

from confapp import conf
from pyforms.basewidget import BaseWidget
from pyforms.controls import ControlMatplotlib
from AnyQt.QtCore import QTimer

# Keeps the trace's own redraw cost bounded regardless of session length (same class of issue
# module_gui.py's live view already works around by keeping only the last 200 samples) -- the
# *visible* x-range is explicitly set to this window each tick (see __on_draw_evt), independently of
# how many marker artists have accumulated, so old markers scrolling out of view don't widen it back
# out.
MAX_PLOTTED_POINTS = 1000


class WheelPositionPlot(BaseWidget):

    COL_MSGTYPE = 0
    COL_PCTIME = 1
    COL_INITTIME = 2
    COL_FINALTIME = 3
    COL_MSG = 4
    COL_INFO = 5

    def __init__(self, session):
        BaseWidget.__init__(self, session.name)
        self.set_margin(5)

        self.session = session

        self._graph = ControlMatplotlib('Value')

        self._timer = QTimer()
        self._timer.timeout.connect(self.update)

        self._rows_read = 0
        self._times = []
        self._positions = []
        self._trial_starts = []
        self._choice_made = []
        self._timed_out = []
        self._left_threshold = None
        self._right_threshold = None

        # Persistent artist state -- created once on first draw, then mutated/appended to rather
        # than recreated every timer tick (see __on_draw_evt).
        self._axes = None
        self._line = None
        self._left_threshold_line = None
        self._right_threshold_line = None
        self._trial_starts_drawn = 0
        self._choice_made_drawn = 0
        self._timed_out_drawn = 0

        self.formset = ['_graph']

        self._graph.on_draw = self.__on_draw_evt

    def show(self, detached=False):
        if self.session.is_running and self.session.setup.detached:
            return

        # Prevent the call to be recursive because of the mdi_area
        if not detached:
            if hasattr(self, '_show_called'):
                BaseWidget.show(self)
                return
            self._show_called = True
            self.mainwindow.mdi_area += self
            del self._show_called
        else:
            BaseWidget.show(self)

        self._stop = False
        if not self._stop and self.session.is_running:
            self._timer.start(conf.WHEELPOSITION_PLUGIN_REFRESH_RATE)

        self.update()

    def hide(self):
        self._timer.stop()
        self._stop = True

    def read_data(self):
        # Slice to only the rows appended since the last call *before* querying -- session.data
        # grows for the life of the session (board_com.py appends one row at a time), so re-running
        # .query() over the whole table on every timer tick gets slower and slower as a session goes
        # on. Rows are only ever appended, never reordered, so an iloc slice by row count is safe.
        total_rows = len(self.session.data)
        if total_rows <= self._rows_read:
            return
        new_rows = self.session.data.iloc[self._rows_read:]
        self._rows_read = total_rows

        res = new_rows.query(
            "TYPE == 'VAL' and MSG in ['WHEEL_POS', 'TRIAL_START', "
            "'CHOICE_MADE', 'TIMED_OUT', 'LEFT_THRESHOLD_DEG', 'RIGHT_THRESHOLD_DEG']")

        for index, msg in res.iterrows():
            name = msg[self.COL_MSG]
            info = msg[self.COL_INFO]

            if name == 'WHEEL_POS':
                t_str, pos_str = str(info).split(',')
                self._times.append(float(t_str))
                self._positions.append(float(pos_str))
            elif name == 'TRIAL_START':
                self._trial_starts.append(float(info))
            elif name == 'CHOICE_MADE':
                self._choice_made.append(float(info))
            elif name == 'TIMED_OUT':
                self._timed_out.append(float(info))
            elif name == 'LEFT_THRESHOLD_DEG':
                self._left_threshold = float(info)
            elif name == 'RIGHT_THRESHOLD_DEG':
                self._right_threshold = float(info)

    def _draw_new_markers(self, axes, values, drawn_count_attr, color):
        """ Appends one axvline per value added since the last call, instead of redrawing the whole
        set every tick -- values only ever grow (trial markers are never retracted), so nothing
        needs to be removed, only added. """
        drawn = getattr(self, drawn_count_attr)
        for t in values[drawn:]:
            axes.axvline(t, color=color, linestyle=':', linewidth=0.6)
        setattr(self, drawn_count_attr, len(values))

    def __on_draw_evt(self, figure):
        try:
            if self._axes is None:
                self._axes = figure.add_subplot(111)
                self._line, = self._axes.plot([], [], color='black', linewidth=0.8)
                self._axes.set_xlabel('Time (s)')
                self._axes.set_ylabel('Position (degrees)')
            axes = self._axes

            if self._left_threshold is not None and self._left_threshold_line is None:
                self._left_threshold_line = axes.axhline(
                    self._left_threshold, color='tab:green', linestyle='--', linewidth=0.8)
            if self._right_threshold is not None and self._right_threshold_line is None:
                self._right_threshold_line = axes.axhline(
                    self._right_threshold, color='tab:red', linestyle='--', linewidth=0.8)

            times = self._times[-MAX_PLOTTED_POINTS:]
            positions = self._positions[-MAX_PLOTTED_POINTS:]
            self._line.set_data(times, positions)

            self._draw_new_markers(axes, self._trial_starts, '_trial_starts_drawn', 'tab:blue')
            self._draw_new_markers(axes, self._choice_made, '_choice_made_drawn', 'gold')
            self._draw_new_markers(axes, self._timed_out, '_timed_out_drawn', 'dimgray')

            # relim()/autoscale_view() pick up the trace + threshold lines' Y extent (axvline
            # markers use axes-relative Y coordinates, so they never affect this); X is set
            # explicitly afterward so old markers that have scrolled out of the trace's own window
            # don't widen the visible range back out.
            axes.relim()
            axes.autoscale_view(scalex=False, scaley=True)
            if times:
                axes.set_xlim(times[0], times[-1])

            self._graph.repaint()
        except Exception:
            self.critical(traceback.format_exc(), 'An error occurred')

    def update(self):
        if not self.session.is_running:
            self._timer.stop()
        self.read_data()
        self._graph.draw()

    @property
    def mainwindow(self):
        return self.session.mainwindow

    @property
    def title(self):
        return BaseWidget.title.fget(self)

    @title.setter
    def title(self, value):
        BaseWidget.title.fset(self, 'Wheel Position: {0}'.format(value))
