# -*- coding: utf-8 -*-
"""
  PyBal unit tests
  ~~~~~~~~~~~~~~~~

  This module contains tests for `pybal.server.Server`.

"""

import mock
import socket

import pybal.server

from twisted.python import failure
from twisted.names.error import AuthoritativeDomainError
from twisted.internet import reactor

from .fixtures import PyBalTestCase, StubLVSService

class ServerTestCase(PyBalTestCase):
    """Test case for `pybal.server.Server`."""

    def setUp(self):
        super(ServerTestCase, self).setUp()

        self.server = pybal.server.Server(
            'example.com', self.lvsservice)

        self.mockMonitor = mock.MagicMock()
        self.mockCoordinator = mock.MagicMock()
        self.server.addMonitor(self.mockMonitor)

        self.exampleConfigDict = {
            'host': "example1.example.com",
            'weight': 66,
            'enabled': True,
            'rogue': "this attribute should not be merged"
        }

    def tearDown(self):
        for call in reactor.getDelayedCalls():
            if call.func.func_name == 'maybeParseConfig':
                call.cancel()

    def testInit(self):
        self.assertEquals(self.server.addressFamily, socket.AF_INET)

        server = pybal.server.Server(
            'example.com', self.lvsservice, addressFamily=socket.AF_INET6)
        self.assertEquals(server.addressFamily, socket.AF_INET6)

    def testEq(self):
        self.assertEquals(self.server, self.server)

        # Create a Server instance with different hostname
        otherServer = pybal.server.Server('other.example.com', self.lvsservice)
        self.assertNotEqual(self.server, otherServer)

        # Create a Server instance with equal hostname but different LVSService
        otherLVSService = StubLVSService(
            'otherservice',
            (self.protocol, self.ip, self.port, self.scheduler),
            self.config)
        otherServer = pybal.server.Server('example.com', otherLVSService)
        self.assertNotEqual(self.server, otherServer)

    def testHash(self):
        # Create a Server instance with different hostname
        otherServer = pybal.server.Server('other.example.com', self.lvsservice)
        self.assertNotEqual(hash(self.server), hash(otherServer))

    def testAddMonitor(self):
        self.assertIn(self.mockMonitor, self.server.monitors)

    def testRemoveMonitors(self):
        self.server.removeMonitors()
        self.assertEqual(len(self.server.monitors), 0)
        self.mockMonitor.stop.assert_called()

    def testResolveHostname(self):
        def callback(result):
            self.assertIsNotNone(result)

        deferred = self.server.resolveHostname()
        deferred.addCallback(callback)
        return deferred

    def testResolveNonexistentHostname(self):
        def errback(err):
            self.assertTrue(isinstance(err, failure.Failure))
            self.assertIsNotNone(err.check(AuthoritativeDomainError))
            return True

        nonexistentServer = pybal.server.Server(
            'nonexistent.example.com', self.lvsservice)
        deferred = nonexistentServer.resolveHostname()
        deferred.addErrback(errback)
        return deferred

    def testDestroy(self):
        self.server.destroy()
        self.assertFalse(self.server.enabled)
        self.assertEqual(len(self.server.monitors), 0)

    def testInitialize(self):
        def cb(result):
            self.assertIsInstance(result, bool)
            self.assertEquals(self.server.ready, result)

        d = self.server.initialize(self.mockCoordinator)
        d.addCallback(cb)
        return d

    @mock.patch('pybal.server.Server.createMonitoringInstances')
    def testReady(self, mock_createMonitoringInstances):
        r = self.server._ready(True, self.mockCoordinator)
        self.assertTrue(r)
        self.assertTrue(self.server.ready)
        mock_createMonitoringInstances.assert_called()

    def testInitFailed(self):
        r = self.server._initFailed(failure.Failure(Exception("Fake failure")))
        self.assertFalse(r)
        self.assertFalse(self.server.ready)

    def testCreateMonitoringInstances(self):
        self.config['monitors'] = "[ \"NonexistentMonitor\" ]"
        self.server.createMonitoringInstances(self.mockCoordinator)

    def testCreateMockMonitoringInstance(self):
        self.config['monitors'] = "[ \"Mock\" ]"
        self.server.createMonitoringInstances(self.mockCoordinator)
        self.assertTrue(self.server.monitors)
        self.assertTrue(all({m.active for m in self.server.monitors}))

    @mock.patch('twisted.internet.reactor.stop')
    @mock.patch('importlib.import_module')
    def testCreateFailingMockMonitoringInstance(self, mocked_import_module, mock_reactor):
        mocked_import_module.side_effect = RuntimeError("Similating runtime error to aid unit testing")
        self.config['monitors'] = "[ \"Mock\" ]"
        self.server.createMonitoringInstances(self.mockCoordinator)
        self.assertTrue(mock_reactor.assert_called)

    @mock.patch('twisted.internet.reactor.stop')
    def testCreateMonitoringInstanceFailure(self, mock_reactor):
        # Test missing 'monitors' option
        assert 'monitors' not in self.config
        self.server.removeMonitors()
        self.server.createMonitoringInstances(self.mockCoordinator)
        self.assertFalse(self.server.monitors)

        # Test a non-list in the configuration
        self.config['monitors'] = "(1,2)"
        self.server.createMonitoringInstances(self.mockCoordinator)
        mock_reactor.assert_called()

    def testCalcStatus(self):
        self.mockMonitor.up = True
        self.assertTrue(self.server.calcStatus())
        self.assertTrue(self.server.calcPartialStatus())

        m = mock.MagicMock()
        m.up = True
        self.server.addMonitor(m)
        self.assertTrue(self.server.calcStatus())
        self.assertTrue(self.server.calcPartialStatus())

        m.up = False
        self.assertFalse(self.server.calcStatus())
        self.assertTrue(self.server.calcPartialStatus())

        self.mockMonitor.up = False
        self.assertFalse(self.server.calcPartialStatus())

        # Currently, no monitors implies False Status
        self.server.removeMonitors()
        self.assertFalse(self.server.calcStatus())
        self.assertTrue(self.server.calcPartialStatus())

    def testTextStatus(self):
        textStatus = self.server.textStatus()
        self.assertTrue(isinstance(textStatus, str))
        self.assertEquals(len(textStatus.split('/')), 3)

    def testMaintainState(self):
        self.server.pool = True
        self.server.enabled = False
        self.server.maintainState()
        self.assertFalse(self.server.pool)

        self.server.pool = False
        self.server.enabled = True
        self.server.maintainState()
        self.assertFalse(self.server.up)

    def testMerge(self):
        # Test whether (only!) valid config keys get
        # set on the Server object
        self.server.merge(self.exampleConfigDict)
        self.assertEquals(self.server.host, self.exampleConfigDict['host'])
        self.assertEquals(self.server.weight, self.exampleConfigDict['weight'])
        self.assertEquals(self.server.enabled, self.exampleConfigDict['enabled'])
        # Make sure invalid config key 'rogue' didn't make it
        self.assertNotIn(self.exampleConfigDict['rogue'], self.server.__dict__)
        del self.exampleConfigDict['rogue']
        # self.server.__dict__ should now be a superset of self.exampleConfigDict,
        # as all remaining keys are valid
        self.assertDictContainsSubset(self.exampleConfigDict, self.server.__dict__)

    def testDumpState(self):
        state = self.server.dumpState()
        self.assertLessEqual(
            {'pooled', 'weight', 'up', 'enabled'},
            set(state.keys()))

    def testBuildServer(self):
        server = self.server.buildServer(
            hostName=self.exampleConfigDict['host'],
            configuration=self.exampleConfigDict,
            lvsservice=mock.MagicMock())
        self.assertTrue(isinstance(server, pybal.server.Server))
        self.assertFalse(server.modified)
