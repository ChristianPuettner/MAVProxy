"""
  MAVProxy console

  uses lib/console.py for display
"""

import os, sys, math, time, re
import traceback

from MAVProxy.modules.lib import wxconsole
from MAVProxy.modules.lib import textconsole
from pymavlink import mavutil
from MAVProxy.modules.lib import mp_util
from MAVProxy.modules.lib import mp_module
from MAVProxy.modules.lib import mp_settings
from MAVProxy.modules.lib import wxsettings
from MAVProxy.modules.lib.mp_menu import *

green = (0, 128, 0)

class DisplayItem:
    def __init__(self, fmt, expression, row):
        self.expression = expression.strip('"\'')
        self.format = fmt.strip('"\'')
        re_caps = re.compile('[A-Z_][A-Z0-9_]+')
        self.msg_types = set(re.findall(re_caps, expression))
        self.row = row

class ConsoleModule(mp_module.MPModule):
    def __init__(self, mpstate):
        super(ConsoleModule, self).__init__(mpstate, "console", "GUI console", public=True, multi_vehicle=True)
        self.in_air = False
        self.start_time = 0.0
        self.total_time = 0.0
        self.speed = 0
        self.max_link_num = 0
        self.last_sys_status_health = 0
        self.last_sys_status_errors_announce = 0
        self.user_added = {}
        self.safety_on = False
        self.unload_check_interval = 5 # seconds
        self.last_unload_check_time = time.time()
        self.add_command('console', self.cmd_console, "console module", ['add','list','remove'])
        mpstate.console = wxconsole.MessageConsole(title='Console')

        # setup some default status information
        mpstate.console.set_status('Mode', 'UNKNOWN', row=0, fg='blue')
        mpstate.console.set_status('SysID', '', row=0, fg='blue')
        mpstate.console.set_status('ARM', 'ARM', fg='grey', row=0)
        mpstate.console.set_status('GPS', 'GPS: --', fg='red', row=0)
        mpstate.console.set_status('GPS2', '', fg='red', row=0)
        mpstate.console.set_status('Vcc', 'Vcc: --', fg='red', row=0)
        mpstate.console.set_status('Radio', 'Radio: --', row=0)
        mpstate.console.set_status('INS', 'INS', fg='grey', row=0)
        mpstate.console.set_status('MAG', 'MAG', fg='grey', row=0)
        mpstate.console.set_status('AS', 'AS', fg='grey', row=0)
        mpstate.console.set_status('RNG', 'RNG', fg='grey', row=0)
        mpstate.console.set_status('AHRS', 'AHRS', fg='grey', row=0)
        mpstate.console.set_status('EKF', 'EKF', fg='grey', row=0)
        mpstate.console.set_status('LOG', 'LOG', fg='grey', row=0)
        mpstate.console.set_status('Heading', 'Hdg ---/---', row=2)
        mpstate.console.set_status('Alt', 'Alt ---', row=2)
        mpstate.console.set_status('AGL', 'AGL ---/---', row=2)
        mpstate.console.set_status('AirSpeed', 'AirSpeed --', row=2)
        mpstate.console.set_status('GPSSpeed', 'GPSSpeed --', row=2)
        mpstate.console.set_status('Thr', 'Thr ---', row=2)
        mpstate.console.set_status('Roll', 'Roll ---', row=2)
        mpstate.console.set_status('Pitch', 'Pitch ---', row=2)
        mpstate.console.set_status('Wind', 'Wind ---/---', row=2)
        mpstate.console.set_status('WP', 'WP --', row=3)
        mpstate.console.set_status('WPDist', 'Distance ---', row=3)
        mpstate.console.set_status('WPBearing', 'Bearing ---', row=3)
        mpstate.console.set_status('AltError', 'AltError --', row=3)
        mpstate.console.set_status('AspdError', 'AspdError --', row=3)
        mpstate.console.set_status('FlightTime', 'FlightTime --', row=3)
        mpstate.console.set_status('ETR', 'ETR --', row=3)
        mpstate.console.set_status('Params', 'Param ---/---', row=3)
        mpstate.console.set_status('Mission', 'Mission --/--', row=3)

        self.console_settings = mp_settings.MPSettings([
            ('debug_level', int, 0),
        ])

        self.vehicle_list = []
        self.vehicle_heartbeats = {}  # map from (sysid,compid) tuple to most recent HEARTBEAT nessage
        self.vehicle_menu = None
        self.vehicle_name_by_sysid = {}
        self.component_name = {}
        self.last_param_sysid_timestamp = None
        self.flight_information = {}

        # create the main menu
        if mp_util.has_wxpython:
            self.menu = MPMenuTop([])
            self.add_menu(MPMenuSubMenu('MAVProxy',
                                        items=[MPMenuItem('Settings', 'Settings', 'menuSettings'),
                                               MPMenuItem('Show Map', 'Load Map', '# module load map'),
                                               MPMenuItem('Show HUD', 'Load HUD', '# module load horizon'),
                                               MPMenuItem('Show Checklist', 'Load Checklist', '# module load checklist')]))
            self.vehicle_menu = MPMenuSubMenu('Vehicle', items=[])
            self.add_menu(self.vehicle_menu)

        self.shown_agl = False

    def cmd_console(self, args):
        usage = 'usage: console <add|list|remove|menu|set>'
        if len(args) < 1:
            print(usage)
            return
        cmd = args[0]
        if cmd == 'add':
            if len(args) < 4:
                print("usage: console add ID FORMAT EXPRESSION <row>")
                return
            if len(args) > 4:
                row = int(args[4])
            else:
                row = 4
            self.user_added[args[1]] = DisplayItem(args[2], args[3], row)
            self.console.set_status(args[1], "", row=row)
        elif cmd == 'list':
            for k in sorted(self.user_added.keys()):
                d = self.user_added[k]
                print("%s : FMT=%s EXPR=%s ROW=%u" % (k, d.format, d.expression, d.row))
        elif cmd == 'remove':
            if len(args) < 2:
                print("usage: console remove ID")
                return
            id = args[1]
            if id in self.user_added:
                self.user_added.pop(id)
        elif cmd == 'menu':
            self.cmd_menu(args[1:])
        elif cmd == 'set':
            self.cmd_set(args[1:])
        else:
            print(usage)

    def add_menu(self, menu):
        '''add a new menu'''
        self.menu.add(menu)
        self.mpstate.console.set_menu(self.menu, self.menu_callback)

    def cmd_menu_add(self, args):
        '''add to console menus'''
        if len(args) < 2:
            print("Usage: console menu add MenuPath command")
            return
        menupath = args[0].strip('"').split(':')
        name = menupath[-1]
        cmd = '# ' + ' '.join(args[1:])
        self.menu.add_to_submenu(menupath[:-1], MPMenuItem(name, name, cmd))
        self.mpstate.console.set_menu(self.menu, self.menu_callback)

    def cmd_menu(self, args):
        '''control console menus'''
        if len(args) < 2:
            print("Usage: console menu <add>")
            return
        if args[0] == 'add':
            self.cmd_menu_add(args[1:])

    def cmd_set(self, args):
        '''set console options'''
        self.console_settings.command(args)

    def remove_menu(self, menu):
        '''add a new menu'''
        self.menu.remove(menu)
        self.mpstate.console.set_menu(self.menu, self.menu_callback)

    def unload(self):
        '''unload module'''
        self.mpstate.console.close()
        self.mpstate.console = textconsole.SimpleConsole()

    def menu_callback(self, m):
        '''called on menu selection'''
        if m.returnkey.startswith('# '):
            cmd = m.returnkey[2:]
            if m.handler is not None:
                if m.handler_result is None:
                    return
                cmd += m.handler_result
            self.mpstate.functions.process_stdin(cmd)
        if m.returnkey == 'menuSettings':
            wxsettings.WXSettings(self.settings)


    def estimated_time_remaining(self, lat, lon, wpnum, speed):
        '''estimate time remaining in mission in seconds'''
        if self.module('wp') is None:
            return 0
        idx = wpnum
        if wpnum >= self.module('wp').wploader.count():
            return 0
        distance = 0
        done = set()
        while idx < self.module('wp').wploader.count():
            if idx in done:
                break
            done.add(idx)
            w = self.module('wp').wploader.wp(idx)
            if w.command == mavutil.mavlink.MAV_CMD_DO_JUMP:
                idx = int(w.param1)
                continue
            idx += 1
            if (w.x != 0 or w.y != 0) and w.command in [mavutil.mavlink.MAV_CMD_NAV_WAYPOINT,
                                                        mavutil.mavlink.MAV_CMD_NAV_LOITER_UNLIM,
                                                        mavutil.mavlink.MAV_CMD_NAV_LOITER_TURNS,
                                                        mavutil.mavlink.MAV_CMD_NAV_LOITER_TIME,
                                                        mavutil.mavlink.MAV_CMD_NAV_LAND,
                                                        mavutil.mavlink.MAV_CMD_NAV_TAKEOFF]:
                distance += mp_util.gps_distance(lat, lon, w.x, w.y)
                lat = w.x
                lon = w.y
                if w.command == mavutil.mavlink.MAV_CMD_NAV_LAND:
                    break
        return distance / speed

    def vehicle_type_string(self, hb):
        '''return vehicle type string from a heartbeat'''
        if hb.type in [mavutil.mavlink.MAV_TYPE_FIXED_WING,
                            mavutil.mavlink.MAV_TYPE_VTOL_DUOROTOR,
                            mavutil.mavlink.MAV_TYPE_VTOL_QUADROTOR,
                            mavutil.mavlink.MAV_TYPE_VTOL_TILTROTOR]:
            return 'Plane'
        if hb.type == mavutil.mavlink.MAV_TYPE_GROUND_ROVER:
            return 'Rover'
        if hb.type == mavutil.mavlink.MAV_TYPE_SURFACE_BOAT:
            return 'Boat'
        if hb.type == mavutil.mavlink.MAV_TYPE_SUBMARINE:
            return 'Sub'
        if hb.type in [mavutil.mavlink.MAV_TYPE_QUADROTOR,
                           mavutil.mavlink.MAV_TYPE_COAXIAL,
                           mavutil.mavlink.MAV_TYPE_HEXAROTOR,
                           mavutil.mavlink.MAV_TYPE_OCTOROTOR,
                           mavutil.mavlink.MAV_TYPE_TRICOPTER,
                           mavutil.mavlink.MAV_TYPE_DODECAROTOR]:
            return "Copter"
        if hb.type == mavutil.mavlink.MAV_TYPE_HELICOPTER:
            return "Heli"
        if hb.type == mavutil.mavlink.MAV_TYPE_ANTENNA_TRACKER:
            return "Tracker"
        if hb.type == mavutil.mavlink.MAV_TYPE_AIRSHIP:
            return "Blimp"
        elif hb.type == mavutil.mavlink.MAV_TYPE_ADSB:
            return "ADSB"
        elif hb.type == mavutil.mavlink.MAV_TYPE_ODID:
            return "ODID"
        return "UNKNOWN(%u)" % hb.type

    def component_type_string(self, hb):
        # note that we rely on vehicle_type_string for basic vehicle types
        if hb.type == mavutil.mavlink.MAV_TYPE_GCS:
            return "GCS"
        elif hb.type == mavutil.mavlink.MAV_TYPE_GIMBAL:
            return "Gimbal"
        elif hb.type == mavutil.mavlink.MAV_TYPE_ONBOARD_CONTROLLER:
            return "CC"
        elif hb.type == mavutil.mavlink.MAV_TYPE_ADSB:
            return "ADSB"
        elif hb.type == mavutil.mavlink.MAV_TYPE_ODID:
            return "ODID"
        elif hb.type == mavutil.mavlink.MAV_TYPE_GENERIC:
            return "Generic"
        return self.vehicle_type_string(hb)

    def update_vehicle_menu(self):
        '''update menu for new vehicles'''
        self.vehicle_menu.items = []
        for s in sorted(self.vehicle_list):
            clist = self.module('param').get_component_id_list(s)
            if len(clist) == 1:
                name = 'SysID %u: %s' % (s, self.vehicle_name_by_sysid[s])
                self.vehicle_menu.items.append(MPMenuItem(name, name, '# vehicle %u' % s))
            else:
                for c in sorted(clist):
                    try:
                        name = 'SysID %u[%u]: %s' % (s, c, self.component_name[s][c])
                    except KeyError as e:
                        name = 'SysID %u[%u]: ?' % (s,c)
                    self.vehicle_menu.items.append(MPMenuItem(name, name, '# vehicle %u:%u' % (s,c)))
        self.mpstate.console.set_menu(self.menu, self.menu_callback)
    
    def add_new_vehicle(self, hb):
        '''add a new vehicle'''
        if hb.type == mavutil.mavlink.MAV_TYPE_GCS:
            return
        sysid = hb.get_srcSystem()
        self.vehicle_list.append(sysid)
        self.vehicle_name_by_sysid[sysid] = self.vehicle_type_string(hb)
        self.update_vehicle_menu()

    def check_critical_error(self, msg):
        '''check for any error bits being set in SYS_STATUS'''
        sysid = msg.get_srcSystem()
        compid = msg.get_srcComponent()
        hb = self.vehicle_heartbeats.get((sysid, compid), None)
        if hb is None:
            return
        # only ArduPilot populates the fields with internal error stuff:
        if hb.autopilot != mavutil.mavlink.MAV_AUTOPILOT_ARDUPILOTMEGA:
            return

        errors = msg.errors_count1 | (msg.errors_count2<<16)
        if errors == 0:
            return
        now = time.time()
        if now - self.last_sys_status_errors_announce > self.mpstate.settings.sys_status_error_warn_interval:
            self.last_sys_status_errors_announce = now
            self.say("Critical failure 0x%x sysid=%u compid=%u" % (errors, sysid, compid))

    def set_component_name(self, sysid, compid, name):
        if sysid not in self.component_name:
            self.component_name[sysid] = {}
        if compid not in self.component_name[sysid]:
            self.component_name[sysid][compid] = name
            self.update_vehicle_menu()

    # this method is called when a HEARTBEAT arrives from any source:
    def handle_heartbeat_anysource(self, msg):
            sysid = msg.get_srcSystem()
            compid = msg.get_srcComponent()
            type = msg.get_type()
            if type == 'HEARTBEAT':
                self.vehicle_heartbeats[(sysid, compid)] = msg
            if not sysid in self.vehicle_list:
                self.add_new_vehicle(msg)
            self.set_component_name(sysid, compid, self.component_type_string(msg))

    # this method is called when a GIMBAL_DEVICE_INFORMATION arrives
    # from any source:
    def handle_gimbal_device_information_anysource(self, msg):
            sysid = msg.get_srcSystem()
            compid = msg.get_srcComponent()
            self.set_component_name(sysid, compid, "%s-%s" %
                                    (msg.vendor_name, msg.model_name))

    def handle_radio_status(self, msg):
            # handle RADIO msgs from all vehicles
            if msg.rssi < msg.noise+10 or msg.remrssi < msg.remnoise+10:
                fg = 'red'
            else:
                fg = 'black'
            self.console.set_status('Radio', 'Radio %u/%u %u/%u' % (msg.rssi, msg.noise, msg.remrssi, msg.remnoise), fg=fg)

    def handle_gps_raw(self, msg):
            master = self.master
            type = msg.get_type()
            if type == 'GPS_RAW_INT':
                field = 'GPS'
                prefix = 'GPS:'
            else:
                field = 'GPS2'
                prefix = 'GPS2'
            nsats = msg.satellites_visible
            fix_type = msg.fix_type
            if fix_type >= 3:
                self.console.set_status(field, '%s OK%s (%u)' % (prefix, fix_type, nsats), fg=green)
            else:
                self.console.set_status(field, '%s %u (%u)' % (prefix, fix_type, nsats), fg='red')
            if type == 'GPS_RAW_INT':
                vfr_hud_heading = master.field('VFR_HUD', 'heading', None)
                if vfr_hud_heading is None:
                    # try to fill it in from GLOBAL_POSITION_INT instead:
                    vfr_hud_heading = master.field('GLOBAL_POSITION_INT', 'hdg', None)
                    if vfr_hud_heading is not None:
                        if vfr_hud_heading == 65535:  # mavlink magic "unknown" value
                            vfr_hud_heading = None
                        else:
                            vfr_hud_heading /= 100
                gps_heading = int(msg.cog * 0.01)
                if vfr_hud_heading is None:
                    vfr_hud_heading = '---'
                else:
                    vfr_hud_heading = '%3u' % vfr_hud_heading
                self.console.set_status('Heading', 'Hdg %s/%3u' %
                                        (vfr_hud_heading, gps_heading))

    def handle_vfr_hud(self, msg):
            master = self.master

            if master.mavlink10():
                alt = master.field('GPS_RAW_INT', 'alt', 0) / 1.0e3
            else:
                alt = master.field('GPS_RAW', 'alt', 0)
            home_lat = None
            home_lng = None
            if  self.module('wp') is not None:
                home = self.module('wp').get_home()
                if home is not None:
                    home_lat = home.x
                    home_lng = home.y

            lat = master.field('GLOBAL_POSITION_INT', 'lat', 0) * 1.0e-7
            lng = master.field('GLOBAL_POSITION_INT', 'lon', 0) * 1.0e-7
            rel_alt = master.field('GLOBAL_POSITION_INT', 'relative_alt', 0) * 1.0e-3
            agl_alt = None
            if self.module('terrain') is not None:
                elevation_model = self.module('terrain').ElevationModel
                if self.settings.basealt != 0:
                    agl_alt = elevation_model.GetElevation(lat, lng)
                    if agl_alt is not None:
                        agl_alt = self.settings.basealt - agl_alt
                else:
                    try:
                        agl_alt_home = elevation_model.GetElevation(home_lat, home_lng)
                    except Exception as ex:
                        print(ex)
                        agl_alt_home = None
                    if agl_alt_home is not None:
                        agl_alt = elevation_model.GetElevation(lat, lng)
                    if agl_alt is not None:
                        agl_alt = agl_alt_home - agl_alt
            vehicle_agl = master.field('TERRAIN_REPORT', 'current_height', None)
            if agl_alt is not None or vehicle_agl is not None or self.shown_agl:
                self.shown_agl = True
                if agl_alt is not None:
                    agl_alt += rel_alt
                    agl_alt = self.height_string(agl_alt)
                else:
                    agl_alt = "---"
                if vehicle_agl is None:
                    vehicle_agl = '---'
                else:
                    vehicle_agl = self.height_string(vehicle_agl)
                self.console.set_status('AGL', 'AGL %s/%s' % (agl_alt, vehicle_agl))
            self.console.set_status('Alt', 'Alt %s' % self.height_string(rel_alt))
            self.console.set_status('AirSpeed', 'AirSpeed %s' % self.speed_string(msg.airspeed))
            self.console.set_status('GPSSpeed', 'GPSSpeed %s' % self.speed_string(msg.groundspeed))
            self.console.set_status('Thr', 'Thr %u' % msg.throttle)

            sysid = msg.get_srcSystem()
            if (sysid not in self.flight_information or
                self.flight_information[sysid].supported != True):
                    self.update_flight_time_from_vfr_hud(msg)

    def update_flight_time_from_vfr_hud(self, msg):
            t = time.localtime(msg._timestamp)
            flying = False
            if self.mpstate.vehicle_type == 'copter':
                flying = self.master.motors_armed()
            else:
                flying = msg.groundspeed > 3
            if flying and not self.in_air:
                self.in_air = True
                self.start_time = time.mktime(t)
            elif flying and self.in_air:
                self.total_time = time.mktime(t) - self.start_time
                self.console.set_status('FlightTime', 'FlightTime %u:%02u' % (int(self.total_time)/60, int(self.total_time)%60))
            elif not flying and self.in_air:
                self.in_air = False
                self.total_time = time.mktime(t) - self.start_time
                self.console.set_status('FlightTime', 'FlightTime %u:%02u' % (int(self.total_time)/60, int(self.total_time)%60))

    def handle_attitude(self, msg):
            self.console.set_status('Roll', 'Roll %u' % math.degrees(msg.roll))
            self.console.set_status('Pitch', 'Pitch %u' % math.degrees(msg.pitch))

    def handle_sys_status(self, msg):
            master = self.master
            sensors = { 'AS'   : mavutil.mavlink.MAV_SYS_STATUS_SENSOR_DIFFERENTIAL_PRESSURE,
                        'MAG'  : mavutil.mavlink.MAV_SYS_STATUS_SENSOR_3D_MAG,
                        'INS'  : mavutil.mavlink.MAV_SYS_STATUS_SENSOR_3D_ACCEL | mavutil.mavlink.MAV_SYS_STATUS_SENSOR_3D_GYRO,
                        'AHRS' : mavutil.mavlink.MAV_SYS_STATUS_AHRS,
                        'RC'   : mavutil.mavlink.MAV_SYS_STATUS_SENSOR_RC_RECEIVER,
                        'TERR' : mavutil.mavlink.MAV_SYS_STATUS_TERRAIN,
                        'RNG'  : mavutil.mavlink.MAV_SYS_STATUS_SENSOR_LASER_POSITION,
                        'LOG'  : mavutil.mavlink.MAV_SYS_STATUS_LOGGING,
                        'PRX'  : mavutil.mavlink.MAV_SYS_STATUS_SENSOR_PROXIMITY,
                        'PRE'  : mavutil.mavlink.MAV_SYS_STATUS_PREARM_CHECK,
                        'FLO'  : mavutil.mavlink.MAV_SYS_STATUS_SENSOR_OPTICAL_FLOW,
            }
            hide_if_not_present = set(['PRE', 'PRX', 'FLO'])
            for s in sensors.keys():
                bits = sensors[s]
                present = ((msg.onboard_control_sensors_present & bits) == bits)
                enabled = ((msg.onboard_control_sensors_enabled & bits) == bits)
                healthy = ((msg.onboard_control_sensors_health & bits) == bits)
                if not present and s in hide_if_not_present:
                    continue
                if not present:
                    fg = 'black'
                elif not enabled:
                    fg = 'grey'
                elif not healthy:
                    fg = 'red'
                else:
                    fg = green
                # for terrain show yellow if still loading
                if s == 'TERR' and fg == green and master.field('TERRAIN_REPORT', 'pending', 0) != 0:
                    fg = 'yellow'
                self.console.set_status(s, s, fg=fg)
            announce_unhealthy = {
                'RC': 'RC',
                'PRE': 'pre-arm',
            }
            for s in announce_unhealthy.keys():
                bits = sensors[s]
                enabled = ((msg.onboard_control_sensors_enabled & bits) == bits)
                healthy = ((msg.onboard_control_sensors_health & bits) == bits)
                was_healthy = ((self.last_sys_status_health & bits) == bits)
                if enabled and not healthy and was_healthy:
                    self.say("%s fail" % announce_unhealthy[s])
            announce_healthy = {
                'PRE': 'pre-arm',
            }
            for s in announce_healthy.keys():
                bits = sensors[s]
                enabled = ((msg.onboard_control_sensors_enabled & bits) == bits)
                healthy = ((msg.onboard_control_sensors_health & bits) == bits)
                was_healthy = ((self.last_sys_status_health & bits) == bits)
                if enabled and healthy and not was_healthy:
                    self.say("%s good" % announce_healthy[s])
            self.last_sys_status_health = msg.onboard_control_sensors_health

            if ((msg.onboard_control_sensors_enabled & mavutil.mavlink.MAV_SYS_STATUS_SENSOR_MOTOR_OUTPUTS) == 0):
                self.safety_on = True
            else:
                self.safety_on = False                

    def handle_wind(self, msg):
            self.console.set_status('Wind', 'Wind %u/%s' % (msg.direction, self.speed_string(msg.speed)))

    def handle_ekf_status_report(self, msg):
            highest = 0.0
            vars = ['velocity_variance',
                    'pos_horiz_variance',
                    'pos_vert_variance',
                    'compass_variance',
                    'terrain_alt_variance']
            for var in vars:
                v = getattr(msg, var, 0)
                highest = max(v, highest)
            if highest >= 1.0:
                fg = 'red'
            elif highest >= 0.5:
                fg = 'orange'
            else:
                fg = green
            self.console.set_status('EKF', 'EKF', fg=fg)

    def handle_power_status(self, msg):
            if msg.Vcc >= 4600 and msg.Vcc <= 5300:
                fg = green
            else:
                fg = 'red'
            self.console.set_status('Vcc', 'Vcc %.2f' % (msg.Vcc * 0.001), fg=fg)
            if msg.flags & mavutil.mavlink.MAV_POWER_STATUS_CHANGED:
                fg = 'red'
            else:
                fg = green
            status = 'PWR:'
            if msg.flags & mavutil.mavlink.MAV_POWER_STATUS_USB_CONNECTED:
                status += 'U'
            if msg.flags & mavutil.mavlink.MAV_POWER_STATUS_BRICK_VALID:
                status += 'B'
            if msg.flags & mavutil.mavlink.MAV_POWER_STATUS_SERVO_VALID:
                status += 'S'
            if msg.flags & mavutil.mavlink.MAV_POWER_STATUS_PERIPH_OVERCURRENT:
                status += 'O1'
            if msg.flags & mavutil.mavlink.MAV_POWER_STATUS_PERIPH_HIPOWER_OVERCURRENT:
                status += 'O2'
            self.console.set_status('PWR', status, fg=fg)
            self.console.set_status('Srv', 'Srv %.2f' % (msg.Vservo*0.001), fg=green)

    # this method is called on receipt of any HEARTBEAT so long as it
    # comes from the device we are interested in
    def handle_heartbeat(self, msg):
            sysid = msg.get_srcSystem()
            compid = msg.get_srcComponent()
            master = self.master

            fmode = master.flightmode
            if self.settings.vehicle_name:
                fmode = self.settings.vehicle_name + ':' + fmode
            self.console.set_status('Mode', '%s' % fmode, fg='blue')
            if len(self.vehicle_list) > 1:
                self.console.set_status('SysID', 'Sys:%u' % sysid, fg='blue')
            if self.master.motors_armed():
                arm_colour = green
            else:
                arm_colour = 'red'
            armstring = 'ARM'
            # add safety switch state
            if self.safety_on:
                armstring += '(SAFE)'
            self.console.set_status('ARM', armstring, fg=arm_colour)
            if self.max_link_num != len(self.mpstate.mav_master):
                for i in range(self.max_link_num):
                    self.console.set_status('Link%u'%(i+1), '', row=1)
                self.max_link_num = len(self.mpstate.mav_master)
            for m in self.mpstate.mav_master:
                if self.mpstate.settings.checkdelay:
                    highest_msec_key = (sysid, compid)
                    linkdelay = (self.mpstate.status.highest_msec.get(highest_msec_key, 0) - m.highest_msec.get(highest_msec_key,0))*1.0e-3
                else:
                    linkdelay = 0
                linkline = "Link %s " % (self.link_label(m))
                fg = 'dark green'
                if m.linkerror:
                    linkline += "down"
                    fg = 'red'
                else:
                    packets_rcvd_percentage = 100
                    if (m.mav_count+m.mav_loss) != 0: #avoid divide-by-zero
                        packets_rcvd_percentage = (100.0 * m.mav_count) / (m.mav_count + m.mav_loss)

                    linkbits = ["%u pkts" % m.mav_count,
                                "%u lost" % m.mav_loss,
                                "%.2fs delay" % linkdelay,
                    ]
                    try:
                        if m.mav.signing.sig_count:
                            # other end is sending us signed packets
                            if not m.mav.signing.secret_key:
                                # we've received signed packets but
                                # can't verify them
                                fg = 'orange'
                                linkbits.append("!KEY")
                            elif not m.mav.signing.sign_outgoing:
                                # we've received signed packets but aren't
                                # signing outselves; this can lead to hairloss
                                fg = 'orange'
                                linkbits.append("!SIGNING")
                            if m.mav.signing.badsig_count:
                                fg = 'orange'
                                linkbits.append("%u badsigs" % m.mav.signing.badsig_count)
                    except AttributeError as e:
                        # mav.signing.sig_count probably doesn't exist
                        pass

                    linkline += "OK {rcv_pct:.1f}% ({bits})".format(
                        rcv_pct=packets_rcvd_percentage,
                        bits=", ".join(linkbits))

                    if linkdelay > 1 and fg == 'dark green':
                        fg = 'orange'

                self.console.set_status('Link%u'%m.linknum, linkline, row=1, fg=fg)

    def handle_mission_current(self, msg):
            master = self.master
            if self.module('wp') is not None:
                wpmax = self.module('wp').wploader.count()
            else:
                wpmax = 0
            if wpmax > 0:
                wpmax = "/%u" % wpmax
            else:
                wpmax = ""
            self.console.set_status('WP', 'WP %u%s' % (msg.seq, wpmax))
            lat = master.field('GLOBAL_POSITION_INT', 'lat', 0) * 1.0e-7
            lng = master.field('GLOBAL_POSITION_INT', 'lon', 0) * 1.0e-7
            if lat != 0 and lng != 0:
                airspeed = master.field('VFR_HUD', 'airspeed', 30)
                if abs(airspeed - self.speed) > 5:
                    self.speed = airspeed
                else:
                    self.speed = 0.98*self.speed + 0.02*airspeed
                self.speed = max(1, self.speed)
                time_remaining = int(self.estimated_time_remaining(lat, lng, msg.seq, self.speed))
                self.console.set_status('ETR', 'ETR %u:%02u' % (time_remaining/60, time_remaining%60))

    def handle_nav_controller_output(self, msg):
            self.console.set_status('WPDist', 'Distance %s' % self.dist_string(msg.wp_dist))
            self.console.set_status('WPBearing', 'Bearing %u' % msg.target_bearing)
            if msg.alt_error > 0:
                alt_error_sign = "(L)"
            else:
                alt_error_sign = "(H)"
            if msg.aspd_error > 0:
                aspd_error_sign = "(L)"
            else:
                aspd_error_sign = "(H)"
            if math.isnan(msg.alt_error):
                alt_error = "NaN"
            else:
                alt_error = "%s%s" % (self.height_string(msg.alt_error), alt_error_sign)
            self.console.set_status('AltError', 'AltError %s' % alt_error)
            self.console.set_status('AspdError', 'AspdError %s%s' % (self.speed_string(msg.aspd_error*0.01), aspd_error_sign))

    def handle_param_value(self, msg):
            rec, tot = self.module('param').param_status()
            self.console.set_status('Params', 'Param %u/%u' % (rec,tot))

    def handle_high_latency2(self, msg):
            self.console.set_status('WPDist', 'Distance %s' % self.dist_string(msg.target_distance * 10))
            # The -180 here for for consistency with NAV_CONTROLLER_OUTPUT (-180->180), whereas HIGH_LATENCY2 is (0->360)
            self.console.set_status('WPBearing', 'Bearing %u' % ((msg.target_heading * 2) - 180))
            alt_error = "%s%s" % (self.height_string(msg.target_altitude - msg.altitude),
                                  "(L)" if (msg.target_altitude - msg.altitude) > 0 else "(L)")
            self.console.set_status('AltError', 'AltError %s' % alt_error)
            self.console.set_status('AspdError', 'AspdError %s%s' % (self.speed_string((msg.airspeed_sp - msg.airspeed)/5),
                                                                    "(L)" if (msg.airspeed_sp - msg.airspeed) > 0 else "(L)"))
            # The -180 here for for consistency with WIND (-180->180), whereas HIGH_LATENCY2 is (0->360)
            self.console.set_status('Wind', 'Wind %u/%s' % ((msg.wind_heading * 2) - 180, self.speed_string(msg.windspeed / 5)))
            self.console.set_status('Alt', 'Alt %s' % self.height_string(msg.altitude - self.module('terrain').ElevationModel.GetElevation(msg.latitude / 1E7, msg.longitude / 1E7)))
            self.console.set_status('AirSpeed', 'AirSpeed %s' % self.speed_string(msg.airspeed / 5))
            self.console.set_status('GPSSpeed', 'GPSSpeed %s' % self.speed_string(msg.groundspeed / 5))
            self.console.set_status('Thr', 'Thr %u' % msg.throttle)
            self.console.set_status('Heading', 'Hdg %s/---' % (msg.heading * 2))
            self.console.set_status('WP', 'WP %u/--' % (msg.wp_num))
            
            #re-map sensors
            sensors = { 'AS'   : mavutil.mavlink.HL_FAILURE_FLAG_DIFFERENTIAL_PRESSURE,
                        'MAG'  : mavutil.mavlink.HL_FAILURE_FLAG_3D_MAG,
                        'INS'  : mavutil.mavlink.HL_FAILURE_FLAG_3D_ACCEL | mavutil.mavlink.HL_FAILURE_FLAG_3D_GYRO,
                        'AHRS' : mavutil.mavlink.HL_FAILURE_FLAG_ESTIMATOR,
                        'RC'   : mavutil.mavlink.HL_FAILURE_FLAG_RC_RECEIVER,
                        'TERR' : mavutil.mavlink.HL_FAILURE_FLAG_TERRAIN
            }
            for s in sensors.keys():
                bits = sensors[s]
                failed = ((msg.failure_flags & bits) == bits)
                if failed:
                    fg = 'red'
                else:
                    fg = green
                self.console.set_status(s, s, fg=fg)
                
            # do the remaining non-standard system mappings
            fence_failed = ((msg.failure_flags & mavutil.mavlink.HL_FAILURE_FLAG_GEOFENCE) == mavutil.mavlink.HL_FAILURE_FLAG_GEOFENCE)
            if fence_failed:
                fg = 'red'
            else:
                fg = green
            self.console.set_status('Fence', 'FEN', fg=fg)
            gps_failed = ((msg.failure_flags & mavutil.mavlink.HL_FAILURE_FLAG_GPS) == mavutil.mavlink.HL_FAILURE_FLAG_GPS)
            if gps_failed:
                self.console.set_status('GPS', 'GPS FAILED', fg='red')
            else:
                self.console.set_status('GPS', 'GPS OK', fg=green)
            batt_failed = ((msg.failure_flags & mavutil.mavlink.HL_FAILURE_FLAG_GPS) == mavutil.mavlink.HL_FAILURE_FLAG_BATTERY)
            if batt_failed:
                self.console.set_status('PWR', 'PWR FAILED', fg='red')
            else:
                self.console.set_status('PWR', 'PWR OK', fg=green)

    def handle_flight_information(self, msg):
        sysid = msg.get_srcSystem()
        if sysid not in self.flight_information:
            self.flight_information[sysid] = ConsoleModule.FlightInformation(sysid)
        self.flight_information[sysid].last_seen = time.time()

        # NOTE! the takeoff_time_utc field is misnamed in the XML!
        if msg.takeoff_time_utc == 0:
            # 0 is "landed", so don't update so we preserve the last
            # flight tiem in the display
            return
        total_time = (msg.time_boot_ms - msg.takeoff_time_utc*0.001) * 0.001
        self.console.set_status('FlightTime', 'FlightTime %u:%02u' % (int(total_time)/60, int(total_time)%60))

    def handle_command_ack(self, msg):
        sysid = msg.get_srcSystem()

        if msg.command != mavutil.mavlink.MAV_CMD_SET_MESSAGE_INTERVAL:
            return

        if sysid not in self.flight_information:
            return

        fi = self.flight_information[sysid]

        if msg.result == mavutil.mavlink.MAV_RESULT_ACCEPTED:
            fi.supported = True
        elif msg.result in [mavutil.mavlink.MAV_RESULT_DENIED, mavutil.mavlink.MAV_RESULT_FAILED]:
            fi.supported = False

    # update user-added console entries; called after a mavlink packet
    # is received:
    def update_user_added_keys(self, msg):
        type = msg.get_type()
        for id in self.user_added.keys():
            if type in self.user_added[id].msg_types:
                d = self.user_added[id]
                try:
                    val = mavutil.evaluate_expression(d.expression, self.master.messages)
                    console_string = d.format % val
                except Exception as ex:
                    console_string = "????"
                    self.console.set_status(id, console_string, row = d.row)
                    if self.console_settings.debug_level > 0:
                        exc_type, exc_value, exc_traceback = sys.exc_info()
                        if self.mpstate.settings.moddebug > 3:
                            traceback.print_exception(
                                exc_type,
                                exc_value,
                                exc_traceback,
                                file=sys.stdout
                            )
                        elif self.mpstate.settings.moddebug > 1:
                            traceback.print_exception(exc_type, exc_value, exc_traceback,
                                                      limit=2, file=sys.stdout)
                        elif self.mpstate.settings.moddebug == 1:
                            print(ex)
                        print(f"{id} failed")
                self.console.set_status(id, console_string, row = d.row)

    def mavlink_packet(self, msg):
        '''handle an incoming mavlink packet'''
        if not isinstance(self.console, wxconsole.MessageConsole):
            return
        if not self.console.is_alive():
            self.mpstate.console = textconsole.SimpleConsole()
            return
        type = msg.get_type()

        if type in frozenset(['HEARTBEAT', 'HIGH_LATENCY2']):
            self.handle_heartbeat_anysource(msg)

        elif type == 'GIMBAL_DEVICE_INFORMATION':
            self.handle_gimbal_device_information_anysource(msg)

        if self.last_param_sysid_timestamp != self.module('param').new_sysid_timestamp:
            '''a new component ID has appeared for parameters'''
            self.last_param_sysid_timestamp = self.module('param').new_sysid_timestamp
            self.update_vehicle_menu()

        if type in ['RADIO', 'RADIO_STATUS']:
            self.handle_radio_status(msg)

        if type == 'SYS_STATUS':
            self.check_critical_error(msg)

        if not self.message_is_from_primary_vehicle(msg):
            # don't process msgs from other than primary vehicle, other than
            # updating vehicle list
            return

        # add some status fields
        if type in [ 'GPS_RAW_INT', 'GPS2_RAW' ]:
            self.handle_gps_raw(msg)

        elif type == 'VFR_HUD':
            self.handle_vfr_hud(msg)

        elif type == 'ATTITUDE':
            self.handle_attitude(msg)

        elif type in ['SYS_STATUS']:
            self.handle_sys_status(msg)

        elif type == 'WIND':
            self.handle_wind(msg)

        elif type == 'EKF_STATUS_REPORT':
            self.handle_ekf_status_report(msg)

        elif type == 'POWER_STATUS':
            self.handle_power_status(msg)

        elif type in ['HEARTBEAT', 'HIGH_LATENCY2']:
            self.handle_heartbeat(msg)

        elif type in ['MISSION_CURRENT']:
            self.handle_mission_current(msg)

        elif type == 'NAV_CONTROLLER_OUTPUT':
            self.handle_nav_controller_output(msg)

        elif type == 'PARAM_VALUE':
            self.handle_param_value(msg)

        # note that we also process this as a HEARTBEAT message above!
        if type == 'HIGH_LATENCY2':
            self.handle_high_latency2(msg)

        elif type == 'FLIGHT_INFORMATION':
            self.handle_flight_information(msg)

        elif type == 'COMMAND_ACK':
            self.handle_command_ack(msg)

        self.update_user_added_keys(msg)

        # we've received a packet from the vehicle; probe for
        # FLIGHT_INFORMATION support:
        self.probe_for_flight_information(msg.get_srcSystem(), msg.get_srcComponent())

    class FlightInformation():
        def __init__(self, sysid):
            self.sysid = sysid
            self.supported = None  # don't know
            self.last_seen = None  # last time we saw FLIGHT_INFORMATION
            self.last_set_message_interval_sent = None  # last time we sent set-interval

    def probe_for_flight_information(self, sysid, compid):
        '''if we don't know if this vehicle supports flight information,
        request it'''
        if sysid not in self.flight_information:
            self.flight_information[sysid] = ConsoleModule.FlightInformation(sysid)

        fi = self.flight_information[sysid]

        now  = time.time()

        if fi.supported is not False and (fi.last_seen is None or now - fi.last_seen > 10):
            # if we stop getting FLIGHT_INFORMATION, re-request it:
            fi.supported = None

        if fi.supported is True or fi.supported is False:
            # we know one way or the other
            return

        # only probe once every 10 seconds
        if (fi.last_set_message_interval_sent is not None and
            now - fi.last_set_message_interval_sent < 10):
            return
        fi.last_set_message_interval_sent = now

        self.master.mav.command_long_send(
            sysid,
            compid,
            mavutil.mavlink.MAV_CMD_SET_MESSAGE_INTERVAL,
            0,  # confirmation
            mavutil.mavlink.MAVLINK_MSG_ID_FLIGHT_INFORMATION,  # msg id
            500000,  # interval - 2Hz
            0,  # p3
            0,  # p4
            0,  # p5
            0,  # p6
            0)  # p7

    def idle_task(self):
        now = time.time()
        if self.last_unload_check_time + self.unload_check_interval < now:
            self.last_unload_check_time = now
            if not self.console.is_alive():
                self.needs_unloading = True

def init(mpstate):
    '''initialise module'''
    return ConsoleModule(mpstate)
