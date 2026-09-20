"""Run only fusion fixtures, with outbound socket connections denied."""
import socket
import sys
import unittest
from unittest.mock import patch


def blocked(*args,**kwargs):
    raise AssertionError('Network is forbidden during offline acceptance')


if __name__=='__main__':
    with patch.object(socket.socket,'connect',blocked), patch.object(socket,'create_connection',blocked):
        suite=unittest.defaultTestLoader.loadTestsFromNames(['test_fusion_shadow','test_fusion_v2','test_dashboard_status_budget','test_fusion_boundaries'])
        result=unittest.TextTestRunner(verbosity=2).run(suite)
    sys.exit(0 if result.wasSuccessful() else 1)
