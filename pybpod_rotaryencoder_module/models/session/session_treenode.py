# !/usr/bin/python3
# -*- coding: utf-8 -*-
import logging

from pybpod_rotaryencoder_module.wheel_position_plot import WheelPositionPlot

logger = logging.getLogger(__name__)


class SessionTreeNode(object):

    def create_treenode(self, tree):
        node = super(SessionTreeNode, self).create_treenode(tree)

        self.wheelposition_action = tree.add_popup_menu_option(
            'Wheel Position',
            self.open_wheelposition_window,
            item=self.node
        )

        return node

    def open_wheelposition_window(self):
        self.load_contents()

        if not hasattr(self, 'wheel_position_win'):
            self.wheel_position_win = WheelPositionPlot(self)
            self.wheel_position_win.show()
        else:
            self.wheel_position_win.show()
