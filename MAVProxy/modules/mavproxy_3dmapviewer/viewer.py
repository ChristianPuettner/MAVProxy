from __future__ import print_function

"""AP_FLAKE8_CLEAN
Viewer process for 3D terrain visualisation.
"""

import numpy as np
from OpenGL import GL, GLU

from MAVProxy.modules.lib.wx_loader import wx
from MAVProxy.modules.mavproxy_map import mp_tile
from MAVProxy.modules.lib import mp_elevation


class MapCanvas(wx.glcanvas.GLCanvas):
    def __init__(self, parent, pipe, service, elevation):
        attribs = [wx.glcanvas.WX_GL_DOUBLEBUFFER, wx.glcanvas.WX_GL_RGBA,
                   wx.glcanvas.WX_GL_DEPTH_SIZE, 16]
        super(MapCanvas, self).__init__(parent, attribList=attribs)
        self.context = wx.glcanvas.GLContext(self)
        self.pipe = pipe
        self.service = service
        self.elevation = mp_elevation.ElevationModel(database=elevation)
        self.tile = mp_tile.MPTile(service=service)
        self.lat = 0
        self.lon = 0
        self.alt_scale = 0.1
        self.size = 512
        self.mesh_res = 64
        self.vertices = None
        self.tex_id = None
        self.path = []
        self.Bind(wx.EVT_PAINT, self.on_paint)
        self.timer = wx.Timer(self)
        self.Bind(wx.EVT_TIMER, self.on_timer, self.timer)
        self.timer.Start(40)

    def load_texture(self):
        area = self.tile.area_to_image(self.lat, self.lon, self.size, self.size, 1000)
        img = np.flipud(area)
        self.tex_id = GL.glGenTextures(1)
        GL.glBindTexture(GL.GL_TEXTURE_2D, self.tex_id)
        GL.glTexParameteri(GL.GL_TEXTURE_2D, GL.GL_TEXTURE_MIN_FILTER, GL.GL_LINEAR)
        GL.glTexParameteri(GL.GL_TEXTURE_2D, GL.GL_TEXTURE_MAG_FILTER, GL.GL_LINEAR)
        GL.glTexImage2D(GL.GL_TEXTURE_2D, 0, GL.GL_RGB, img.shape[1], img.shape[0], 0,
                        GL.GL_BGR, GL.GL_UNSIGNED_BYTE, img)

    def build_mesh(self):
        self.vertices = []
        step = self.size / (self.mesh_res - 1)
        for y in range(self.mesh_res):
            for x in range(self.mesh_res):
                lat, lon = self.tile.coord_from_area(x * step, y * step,
                                                     self.lat, self.lon,
                                                     self.size, 1000)
                alt = self.elevation.GetElevation(lat, lon) or 0
                self.vertices.append((
                    (x / (self.mesh_res - 1) - 0.5) * 2,
                    (y / (self.mesh_res - 1) - 0.5) * 2,
                    alt * self.alt_scale))
        self.vertices = np.array(self.vertices, dtype=np.float32)

    def init_gl(self):
        GL.glEnable(GL.GL_DEPTH_TEST)
        GL.glEnable(GL.GL_TEXTURE_2D)
        GL.glMatrixMode(GL.GL_PROJECTION)
        GL.glLoadIdentity()
        GLU.gluPerspective(45, 1, 0.1, 100)
        GL.glMatrixMode(GL.GL_MODELVIEW)
        GL.glLoadIdentity()
        GL.glTranslatef(0, -1.5, -5)
        self.load_texture()
        self.build_mesh()

    def draw_mesh(self):
        if self.vertices is None:
            return
        GL.glBindTexture(GL.GL_TEXTURE_2D, self.tex_id)
        GL.glBegin(GL.GL_TRIANGLES)
        res = self.mesh_res
        for y in range(res - 1):
            for x in range(res - 1):
                i = y * res + x
                v0 = self.vertices[i]
                v1 = self.vertices[i + 1]
                v2 = self.vertices[i + res]
                v3 = self.vertices[i + res + 1]
                GL.glTexCoord2f(x / (res - 1), y / (res - 1))
                GL.glVertex3f(*v0)
                GL.glTexCoord2f((x + 1) / (res - 1), y / (res - 1))
                GL.glVertex3f(*v1)
                GL.glTexCoord2f(x / (res - 1), (y + 1) / (res - 1))
                GL.glVertex3f(*v2)
                GL.glTexCoord2f((x + 1) / (res - 1), y / (res - 1))
                GL.glVertex3f(*v1)
                GL.glTexCoord2f((x + 1) / (res - 1), (y + 1) / (res - 1))
                GL.glVertex3f(*v3)
                GL.glTexCoord2f(x / (res - 1), (y + 1) / (res - 1))
                GL.glVertex3f(*v2)
        GL.glEnd()

    def draw_path(self):
        if not self.path:
            return
        GL.glColor3f(1, 0, 0)
        GL.glBegin(GL.GL_LINE_STRIP)
        for p in self.path:
            GL.glVertex3f(p[0], p[1], p[2] * self.alt_scale)
        GL.glEnd()
        GL.glColor3f(1, 1, 1)

    def on_paint(self, _):
        if not self.context:
            return
        self.SetCurrent(self.context)
        if self.tex_id is None:
            self.init_gl()
        GL.glClear(GL.GL_COLOR_BUFFER_BIT | GL.GL_DEPTH_BUFFER_BIT)
        GL.glPushMatrix()
        self.draw_mesh()
        self.draw_path()
        GL.glPopMatrix()
        self.SwapBuffers()

    def update_position(self, lat, lon, alt):
        self.lat = lat
        self.lon = lon
        self.path.append((0, 0, alt))

    def on_timer(self, _):
        while self.pipe.poll():
            msg = self.pipe.recv()
            if msg.get('cmd') == 'pos':
                self.update_position(msg['lat'], msg['lon'], msg['alt'])
            elif msg.get('cmd') == 'close':
                wx.GetApp().ExitMainLoop()
                return
        self.Refresh()


def run_viewer(pipe, service, elevation):
    app = wx.App(False)
    frame = wx.Frame(None, title='3D Map Viewer', size=(800, 800))
    MapCanvas(frame, pipe, service, elevation)
    frame.Show()
    pipe.send(dict(cmd='ready'))
    app.MainLoop()
