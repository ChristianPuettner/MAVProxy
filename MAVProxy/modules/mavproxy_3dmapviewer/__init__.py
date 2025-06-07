from __future__ import print_function

"""AP_FLAKE8_CLEAN
3D Map Viewer module using PyOpenGL.
"""

import multiprocessing


from MAVProxy.modules.lib import mp_module

from . import viewer


class Map3DModule(mp_module.MPModule):
    """3D terrain viewer"""

    def __init__(self, mpstate):
        super(Map3DModule, self).__init__(mpstate, "3dmapviewer", "3D map viewer", public=True)
        self.add_command('3dmap', self.cmd_3dmap, "3d map viewer control", ['start', 'stop'])
        self.pipe_parent, self.pipe_child = multiprocessing.Pipe()
        self.process = None
        self.elevation_source = 'SRTM3'
        self.tile_service = 'OpenStreetMap'

    def cmd_3dmap(self, args):
        if len(args) == 0:
            print('usage: 3dmap <start|stop>')
            return
        if args[0] == 'start':
            self.start_viewer()
        elif args[0] == 'stop':
            self.stop_viewer()
        else:
            print('usage: 3dmap <start|stop>')

    def start_viewer(self):
        if self.process and self.process.is_alive():
            print('viewer already running')
            return
        self.process = multiprocessing.Process(
            target=viewer.run_viewer,
            args=(self.pipe_child, self.tile_service, self.elevation_source))
        self.process.daemon = True
        self.process.start()

    def stop_viewer(self):
        if self.process and self.process.is_alive():
            self.pipe_parent.send(dict(cmd='close'))
            self.process.join(1)
            if self.process.is_alive():
                self.process.terminate()
        self.process = None

    def idle_task(self):
        if self.process and self.process.is_alive() and self.pipe_parent.poll():
            msg = self.pipe_parent.recv()
            if msg.get('cmd') == 'ready':
                pass
        if self.process and self.process.is_alive():
            m = self.mpstate.status.msgs.get('GLOBAL_POSITION_INT')
            if m:
                self.pipe_parent.send(dict(cmd='pos', lat=m.lat/1e7, lon=m.lon/1e7, alt=m.relative_alt/1000.0))


def init(mpstate):
    return Map3DModule(mpstate)
