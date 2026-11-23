"""
Raspberry Pi 4 VESA sandwich bracket + LD2410C sensor pod
Parametric CadQuery model.
Run:  python3 pi_vesa_bracket.py [output_dir]   (default: ../ , i.e. hardware/stl and hardware/step)

Coordinate system (bracket):
  X = horizontal, Y = vertical (+ up), Z = thickness.
  Z = 0 is the face that touches the monitor; Z = PLATE_T faces the wall mount / arm.
  The Pi sits on the rear face of a wing that hangs below the VESA plate,
  outside the mount's footprint, with its USB-C / micro-HDMI edge facing down.
"""
import os
import cadquery as cq

# ---------------- Parameters (all mm) ----------------
VESA = 100.0            # primary VESA pattern
INCLUDE_VESA75 = True   # also drill a 75 x 75 pattern
VESA_HOLE_D = 4.6       # M4 clearance
PLATE = 125.0           # square plate size (should be >= your mount's plate)
PLATE_T = 5.0           # plate thickness -> add this to your VESA screw length
CORNER_R = 6.0
CENTER_CUTOUT_D = 60.0  # lightening hole (0 to disable)
FRONT_SPACER = 0.0      # bosses on the monitor side around each hole, for recessed VESA areas

WING_GAP = 6.0          # space between plate bottom edge and the top of the Pi
WING_LEFT_MARGIN = 9.0  # extra wing to the left of the Pi for tie slots / microSD clearance

# Raspberry Pi 4
PI_W, PI_H = 85.0, 56.0
PI_HOLES = [(3.5, 3.5), (61.5, 3.5), (3.5, 52.5), (61.5, 52.5)]  # from bottom-left (port edge = bottom)
PI_OVERHANG = 1.0       # board's port edge hangs this far past the wing so plugs clear
STANDOFF_D = 6.5
STANDOFF_H = 6.0
USE_HEAT_SET = True     # True: M2.5 heat-set insert holes; False: M2.5 self-tap holes
INSERT_D, INSERT_DEPTH = 3.6, 5.0
SELFTAP_D = 2.2

# LD2410C pod
PCB_W, PCB_H, PCB_T = 22.0, 16.0, 1.2
POD_CLEAR = 0.4         # clearance around the PCB
POD_WALL = 2.0
POD_FRONT = 1.2         # thin front wall the radar looks through (no metal, no paint)
POD_DEPTH = 18.0        # internal depth behind the front wall (header + dupont housings)
CAP_LIP = 3.0
CAP_FIT = 0.2           # per-side gap between cap lip and pod walls
CABLE_NOTCH_W, CABLE_NOTCH_H = 7.0, 4.0

OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")


def bracket():
    half = PLATE / 2
    wing_top = -half
    wing_bottom = wing_top - WING_GAP - PI_H + PI_OVERHANG
    pi_x0 = -PI_W / 2
    pi_y0 = wing_bottom - PI_OVERHANG          # bottom (port) edge of the board
    wing_x0 = pi_x0 - WING_LEFT_MARGIN
    wing_x1 = pi_x0 + PI_W                     # flush with USB/Ethernet end

    plate = (cq.Workplane("XY").rect(PLATE, PLATE).extrude(PLATE_T)
             .edges("|Z").fillet(CORNER_R))
    wing_h = wing_top - wing_bottom + CORNER_R  # overlap into plate for a clean union
    wing = (cq.Workplane("XY")
            .center((wing_x0 + wing_x1) / 2, wing_bottom + wing_h / 2)
            .rect(wing_x1 - wing_x0, wing_h).extrude(PLATE_T)
            .edges("|Z and <Y").fillet(CORNER_R))
    body = plate.union(wing)

    # Inside-corner gussets where the wing meets the plate (both sides) are implicit:
    # the plate is wider than the wing, so add a fillet-like triangle on each side.
    # gusset size is limited so it never pokes past the plate's rounded corner
    for sx, xedge in ((-1, wing_x0), (1, wing_x1)):
        room = (half - CORNER_R) - abs(xedge)
        gs = max(0.0, min(10.0, room))
        if gs < 1.0:
            continue
        g = (cq.Workplane("XY")
             .polyline([(xedge, wing_top), (xedge + sx * gs, wing_top), (xedge, wing_top - gs)])
             .close().extrude(PLATE_T))
        body = body.union(g)

    # VESA holes
    pts = [(sx * VESA / 2, sy * VESA / 2) for sx in (-1, 1) for sy in (-1, 1)]
    if INCLUDE_VESA75:
        pts += [(sx * 37.5, sy * 37.5) for sx in (-1, 1) for sy in (-1, 1)]
    if FRONT_SPACER > 0:
        bosses = (cq.Workplane("XY").workplane(offset=-FRONT_SPACER)
                  .pushPoints(pts).circle(5.5).extrude(FRONT_SPACER))
        body = body.union(bosses)
    holes = (cq.Workplane("XY").workplane(offset=-FRONT_SPACER - 1)
             .pushPoints(pts).circle(VESA_HOLE_D / 2).extrude(PLATE_T + FRONT_SPACER + 2))
    body = body.cut(holes)

    if CENTER_CUTOUT_D > 0:
        body = body.cut(cq.Workplane("XY").workplane(offset=-1)
                        .circle(CENTER_CUTOUT_D / 2).extrude(PLATE_T + 2))

    # Pi standoffs on the rear face
    so_pts = [(pi_x0 + hx, pi_y0 + hy) for hx, hy in PI_HOLES]
    standoffs = (cq.Workplane("XY").workplane(offset=PLATE_T)
                 .pushPoints(so_pts).circle(STANDOFF_D / 2).extrude(STANDOFF_H))
    body = body.union(standoffs)
    hole_d = INSERT_D if USE_HEAT_SET else SELFTAP_D
    depth = INSERT_DEPTH if USE_HEAT_SET else STANDOFF_H + PLATE_T - 1.0
    body = body.cut(cq.Workplane("XY").workplane(offset=PLATE_T + STANDOFF_H - depth)
                    .pushPoints(so_pts).circle(hole_d / 2).extrude(depth + 1))

    # Vent slots through the wing under the Pi (clear of the standoffs)
    vent_y0, vent_y1 = pi_y0 + 10, pi_y0 + PI_H - 10
    vent_xs = [pi_x0 + x for x in (16, 25, 34, 43, 52, 70)]
    for vx in vent_xs:
        slot = (cq.Workplane("XY").workplane(offset=-1)
                .center(vx, (vent_y0 + vent_y1) / 2)
                .slot2D(vent_y1 - vent_y0, 3.0, angle=90).extrude(PLATE_T + 2))
        body = body.cut(slot)

    # Cable-tie slots in the left margin (sensor lead, power cable)
    for ty in (pi_y0 + 12, pi_y0 + 42):
        for tx in (wing_x0 + 3.0, wing_x0 + 7.5):
            body = body.cut(cq.Workplane("XY").workplane(offset=-1)
                            .center(tx, ty).rect(2.2, 5.0).extrude(PLATE_T + 2))

    return body, dict(pi_x0=pi_x0, pi_y0=pi_y0, wing_bottom=wing_bottom,
                      total_h=half - wing_bottom)


def sensor_pod():
    iw, ih = PCB_W + 2 * POD_CLEAR, PCB_H + 2 * POD_CLEAR
    ow, oh = iw + 2 * POD_WALL, ih + 2 * POD_WALL
    od = POD_FRONT + POD_DEPTH
    # Z = 0 is the front face (print it face-down). +Y face sticks to the monitor's underside.
    shell = cq.Workplane("XY").box(ow, oh, od, centered=(True, True, False))
    shell = shell.edges("|Z").fillet(1.5)
    cavity = (cq.Workplane("XY").workplane(offset=POD_FRONT)
              .rect(iw, ih).extrude(POD_DEPTH + 1))
    shell = shell.cut(cavity)
    # shallow locating pocket in the front wall's inner face (0.5 mm), keeps the PCB square
    shell = shell.cut(cq.Workplane("XY").workplane(offset=POD_FRONT - 0.5)
                      .rect(iw, ih).extrude(0.6))

    cap = cq.Workplane("XY").box(ow, oh, 2.0, centered=(True, True, False)).edges("|Z").fillet(1.5)
    lip = (cq.Workplane("XY").workplane(offset=2.0)
           .rect(iw - 2 * CAP_FIT, ih - 2 * CAP_FIT).extrude(CAP_LIP)
           .faces(">Z").shell(-1.2))
    cap = cap.union(lip)
    # cable notch on the bottom edge (-Y), through the plate and lip
    notch = (cq.Workplane("XY").center(0, -oh / 2 + CABLE_NOTCH_H / 2 - 0.01)
             .rect(CABLE_NOTCH_W, CABLE_NOTCH_H + POD_WALL).extrude(2.0 + CAP_LIP + 1))
    cap = cap.cut(notch)
    return shell, cap


# Screw variants exported by default: (file suffix, heat-set?, insert hole d, insert depth, self-tap pilot d)
VARIANTS = [
    ("M2.5_heatset", True, 3.6, 5.0, None),
    ("M2.5_selftap", False, None, None, 2.2),
    ("M2_heatset", True, 3.2, 4.0, None),
    ("M2_selftap", False, None, None, 1.75),
]


if __name__ == "__main__":
    import sys
    out = sys.argv[1] if len(sys.argv) > 1 else OUT
    stl_dir, step_dir = os.path.join(out, "stl"), os.path.join(out, "step")
    os.makedirs(stl_dir, exist_ok=True)
    os.makedirs(step_dir, exist_ok=True)

    def export(part, name):
        cq.exporters.export(part, os.path.join(stl_dir, f"{name}.stl"), tolerance=0.05, angularTolerance=0.2)
        cq.exporters.export(part, os.path.join(step_dir, f"{name}.step"))

    for suffix, heat, ins_d, ins_depth, st_d in VARIANTS:
        USE_HEAT_SET = heat
        if heat:
            INSERT_D, INSERT_DEPTH = ins_d, ins_depth
        else:
            SELFTAP_D = st_d
        b, info = bracket()
        export(b, f"pi4_vesa_bracket_{suffix}")
        print(f"pi4_vesa_bracket_{suffix}: wing hangs {-info['wing_bottom']:.1f} mm below VESA centre")
    pod, cap = sensor_pod()
    export(pod, "ld2410c_pod")
    export(cap, "ld2410c_pod_cap")
