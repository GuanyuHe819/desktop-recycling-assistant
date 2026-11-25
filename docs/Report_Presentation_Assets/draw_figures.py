"""Draw the report's vector engineering diagrams and evaluation figures."""
import json
import math
from pathlib import Path

from reportlab.lib.colors import HexColor
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.pdfgen import canvas

OUT = Path(__file__).resolve().parent
W, H = 1280, 720
INK = "#152B2B"
MUTED = "#4B6263"
GREEN = "#156855"
TEAL = "#157F80"
BLUE = "#315778"
RED = "#9C4540"
GOLD = "#8E672A"
EDGE = "#CCD9D5"
PALE = "#EFF5F2"
BG = "#F8FAF9"
WHITE = "#FFFFFF"
for name, file in (("UI", "segoeui.ttf"), ("UIB", "segoeuib.ttf")):
    pdfmetrics.registerFont(TTFont(name, f"C:/Windows/Fonts/{file}"))


class Figure:
    def __init__(self, name, title, subtitle):
        self.c = canvas.Canvas(str(OUT / f"{name}.pdf"), pagesize=(W, H))
        self.c.setTitle(title)
        self.box(0, 0, W, H, BG, radius=0)
        self.text(44, 57, title, 36, bold=True)
        self.text(45, 88, subtitle, 17, color=MUTED)

    def text(self, x, y, value, size=16, *, bold=False, color=INK, anchor="left"):
        self.c.setFont("UIB" if bold else "UI", size)
        self.c.setFillColor(HexColor(color))
        writer = {"left": self.c.drawString, "center": self.c.drawCentredString, "right": self.c.drawRightString}
        writer[anchor](x, H - y, value)

    def box(self, x, y, w, h, fill=WHITE, stroke=None, radius=10):
        self.c.setFillColor(HexColor(fill))
        self.c.setStrokeColor(HexColor(stroke or fill))
        self.c.setLineWidth(1)
        self.c.roundRect(x, H-y-h, w, h, radius, fill=1, stroke=bool(stroke))

    def line(self, points, color=GREEN, width=2, arrow=False):
        self.c.setStrokeColor(HexColor(color))
        self.c.setLineWidth(width)
        path = self.c.beginPath()
        x, y = points[0]
        path.moveTo(x, H-y)
        for x, y in points[1:]:
            path.lineTo(x, H-y)
        if arrow:
            px, py = points[-2]
            angle = math.atan2(y-py, x-px)
            for side in (-.55, .55):
                path.moveTo(x, H-y)
                path.lineTo(x-10*math.cos(angle+side), H-y+10*math.sin(angle+side))
        self.c.drawPath(path)

    def dot(self, x, y, color, radius=4):
        self.c.setFillColor(HexColor(color))
        self.c.circle(x, H-y, radius, stroke=0, fill=1)

    def save(self):
        self.c.showPage()
        self.c.save()


def architecture():
    f = Figure("01_System_Architecture", "Recycling assistant architecture",
               "The desktop predicts a material and the bin controller checks which sensor triggers.")
    f.box(44, 124, 353, 480, WHITE, EDGE)
    f.box(819, 124, 417, 480, WHITE, EDGE)
    f.text(67, 165, "Laptop", 24, bold=True)
    f.text(842, 165, "Physical prototype", 24, bold=True)
    for x, y, title, line1, line2, width in (
        (67, 189, "Camera", "Show one item at a time", "Empty scene resets the cycle", 307),
        (67, 306, "Keras classifier", "224 × 224 RGB input", "8 stable frames at confidence ≥ 0.85", 307),
        (67, 423, "Dashboard and CSV", "Recommendation, feedback and totals", "App acknowledges each received event", 307),
        (843, 189, "Nano RP2040 Connect", "Arms only when bins are clear", "Compares recommended and triggered bins", 369),
        (843, 306, "5 × VL53L0X sensors", "Shared I²C with separate XSHUT lines", "Valid distance ≤ 100 mm triggers an event", 369),
        (843, 423, "5 RGB modules, OLED and buzzer", "Green guides the user to the recommended bin", "Match or mismatch feedback after the trigger", 369),
    ):
        f.box(x, y, width, 91, PALE)
        f.text(x+14, y+26, title, 18, bold=True)
        f.text(x+14, y+49, line1, 15)
        f.text(x+14, y+72, line2, 15)
    f.line([(220, 281), (220, 303)], arrow=True)
    f.line([(220, 398), (220, 420)], arrow=True)
    f.line([(1030, 305), (1030, 283)], color=TEAL, arrow=True)
    f.line([(1207, 236), (1223, 236), (1223, 469), (1213, 469)], arrow=True)
    f.box(499, 292, 230, 92, "#DFEEE8", "#AECAC0")
    f.text(614, 329, "Local MQTT broker", 22, bold=True, anchor="center")
    f.text(614, 356, "Wi-Fi link to the Nano", 16, color=MUTED, anchor="center")
    f.line([(397, 318), (499, 318)], arrow=True)
    f.line([(730, 318), (776, 318), (776, 224), (818, 224)], arrow=True)
    f.line([(818, 261), (793, 261), (793, 354), (731, 354)], color=BLUE, arrow=True)
    f.line([(498, 354), (397, 354)], color=BLUE, arrow=True)
    f.line([(519, 212), (550, 212)], arrow=True)
    f.text(562, 218, "/command · arm and event ACK", 16)
    f.line([(519, 251), (550, 251)], color=BLUE, arrow=True)
    f.text(562, 257, "/event · status, arm ACK and result", 16)
    f.text(67, 564, "Inference, preview and storage stay on the laptop.", 13, color=MUTED)
    f.text(843, 564, "Sensor events report the entry where proximity was detected.", 13, color=MUTED)
    f.box(44, 626, 1192, 55, "#E6F0EC")
    f.text(67, 660, "Show one item    →    Follow the green light    →    Trigger one bin sensor    →    Read the result    →    Clear the station", 19, bold=True)
    f.save()


def wiring():
    burgundy, slate, grey = "#A91F3D", "#202A35", "#65707B"
    supply, ground, data, clock = burgundy, "#525E6B", "#15766B", "#476C87"
    f = Figure("02_Controller_Wiring", "Five-bin prototype wiring",
               "Nano RP2040 Connect, OLED, five sensor and indicator pairs, and a buzzer")
    f.line([(44, 107), (1236, 107)], color=burgundy, width=2)

    f.box(44, 126, 236, 213, WHITE, "#D8DDE2")
    f.text(61, 157, "Arduino Nano", 22, bold=True, color=slate)
    f.text(62, 181, "RP2040 Connect", 16, color=grey)
    f.box(64, 200, 75, 108, slate, radius=6)
    f.box(86, 190, 31, 20, "#CBD2DA", radius=3)
    f.box(85, 238, 33, 34, "#485463", radius=2)
    f.text(101, 294, "USB power", 10, color=WHITE, anchor="center")
    f.box(935, 126, 301, 213, WHITE, "#D8DDE2")
    f.text(952, 157, "OLED display", 22, bold=True, color=slate)
    f.text(952, 181, "SSD1306  ·  I²C 0x3C", 16, color=grey)
    f.box(1061, 207, 151, 100, "#34485E", radius=7)
    f.box(1072, 219, 129, 64, "#182C41", radius=3)
    f.text(1137, 258, "OLED", 21, bold=True, color="#BCDCFF", anchor="center")
    for y, start, end, purpose, color in (
        (208, "3V3", "VCC", "3.3 V supply", supply),
        (240, "GND", "GND", "Common ground", ground),
        (272, "A4 / SDA", "SDA", "I²C data", data),
        (304, "A5 / SCL", "SCL", "I²C clock", clock),
    ):
        f.text(263, y+5, start, 16, bold=True, color=slate, anchor="right")
        f.line([(280, y), (935, y)], color=color, width=2.6)
        f.dot(280, y, color, 4)
        f.dot(935, y, color, 4)
        f.text(605, y-6, purpose, 14, color=color, anchor="center")
        f.text(953, y+5, end, 16, bold=True, color=slate)
    f.text(44, 366, "The OLED and all five ToF sensors share the same four nets.", 16, bold=True, color=slate)

    f.box(44, 389, 715, 236, WHITE, "#D8DDE2")
    f.text(62, 420, "Dedicated pins by bin", 22, bold=True, color=slate)
    columns = [62, 271, 418, 551, 639]
    for x, label in zip(columns, ("Bin / material", "ToF XSHUT", "ToF address", "RGB R", "RGB G")):
        f.text(x, 450, label, 15, bold=True, color=grey)
    f.line([(62, 458), (740, 458)], color="#D8DDE2", width=1)
    for i, (name, pin) in enumerate(zip(("Paper", "Plastic", "Metal", "Cardboard", "Textile"), ("D12", "A0", "A1", "A2", "A3"))):
        y = 484+27*i
        for x, value in zip(columns, (f"{i+1}   {name}", pin, f"0x{0x30+i:02X}", f"D{2+2*i}", f"D{3+2*i}")):
            f.text(x, y, value, 16, bold=x==columns[0], color=slate)
        if i < 4:
            f.line([(62, y+8), (740, y+8)], color="#D8DDE2", width=.65)

    f.box(781, 389, 455, 236, WHITE, "#D8DDE2")
    f.text(800, 420, "Peripheral terminals", 22, bold=True, color=slate)
    f.text(800, 449, "VL53L0X per bin", 16, bold=True, color=slate)
    f.text(800, 473, "VIN–3V3   GND–GND   SDA–A4   SCL–A5", 15, color=slate)
    f.text(800, 501, "RGB per bin", 16, bold=True, color=slate)
    f.text(800, 526, "3V3", 15, bold=True, color=supply)
    f.line([(837, 521), (860, 521)], color=supply, width=1.8)
    f.box(860, 514, 49, 14, WHITE, supply, radius=0)
    f.line([(909, 521), (933, 521)], color=supply, width=1.8)
    f.text(941, 526, "U", 15, bold=True, color=supply)
    f.text(962, 526, "one series resistor per module", 14, color=grey)
    f.text(800, 559, "Buzzer", 16, bold=True, color=slate)
    f.text(800, 583, "VCC–3V3   GND–GND   I/O–D1 (physical TX)", 15, color=slate)
    f.text(800, 606, "RGB R/G and buzzer use active-low outputs.", 14, color=grey)

    f.text(44, 651, "The firmware assigns the five 7-bit I²C addresses at boot. ToF GPIO1 and RGB B are unused.", 15, bold=True, color=slate)
    f.text(44, 675, "Pins are shown by signal name. Identify the OLED terminals by their printed labels.", 14, color=grey)
    f.save()


def recall(metrics):
    cohort = metrics["cohorts"]["public_candidate"]
    baseline, augmented = cohort["baseline"], cohort["augmented"]
    f = Figure("03_Public_Class_Recall", "Material recall on the same public images",
               f"Two exported models · {baseline['n']:,} RealWaste candidate holdout images · Top-1 predictions before the confidence gate")
    f.dot(879, 130, BLUE, 6)
    f.text(893, 136, "Baseline", 16)
    f.dot(1032, 130, GREEN, 6)
    f.text(1046, 136, "With desktop samples", 16)
    left, right = 267, 1145
    for pct in range(0, 101, 20):
        x = left + (right-left)*pct/100
        f.line([(x, 170), (x, 580)], color=EDGE, width=.8)
        f.text(x, 605, f"{pct}%", 14, anchor="center", color=MUTED)
    for i, label in enumerate(metrics["label_order"][:-1]):
        y = 193+80*i
        first, second = baseline["classes"][label], augmented["classes"][label]
        f.text(44, y+24, label.title(), 21, bold=True)
        f.text(45, y+45, f"n = {first['n']:,}", 14, color=MUTED)
        for j, (row, color) in enumerate(((first, BLUE), (second, GREEN))):
            yy = y+j*24
            width = (right-left)*row["recall"]
            if width:
                f.box(left, yy, width, 16, color, radius=3)
            f.text(left+width+8, yy+13, f"{100*row['recall']:.1f}%", 14, bold=True, color=color)
    f.text(44, 654, "File-disjoint from known training folders after hash screening. Object-level independence and training membership are unverified.", 15, color=MUTED)
    f.text(44, 676, "Source images: RealWaste (Single, Iranmanesh & Raad, 2023), UCI, CC BY 4.0. Predictions were generated locally.", 14, color=MUTED)
    f.save()


def threshold(metrics):
    cohort = metrics["cohorts"]["public_candidate"]
    f = Figure("04_Confidence_Thresholds", "Confidence gate trade-off",
               f"Same {cohort['baseline']['n']:,} public images · One prediction per image · Predictions of empty do not recommend a material bin")
    for name, title, x in (("coverage", "Share of all images accepted", 44),
                           ("accuracy_on_accepted", "Accuracy among accepted images", 662)):
        f.box(x, 125, 574, 472, WHITE, EDGE)
        f.text(x+18, 162, title, 20, bold=True)
        left, top, low, span = x+67, 214, 548, 456
        marks = [left+span*(t-.70)/.25 for t in (.70, .80, .85, .90, .95)]
        for level in range(0, 101, 20):
            y = low-(low-top)*level/100
            f.line([(left, y), (left+span, y)], color=EDGE, width=.8)
            f.text(left-8, y+5, f"{level}%", 13, color=MUTED, anchor="right")
        f.line([(marks[2], top), (marks[2], low)], color=GOLD, width=1.4)
        f.text(marks[2], 202, "Deployed", 13, color=GOLD, anchor="center")
        for tag, color in (("baseline", BLUE), ("augmented", GREEN)):
            rows = cohort[tag]["thresholds"]
            p = [(mark, low-(low-top)*row[name]) for mark, row in zip(marks, rows)]
            f.line(p, color=color, width=3)
            for a, b in p:
                f.dot(a, b, color, 4)
        for mark, row in zip(marks, cohort["baseline"]["thresholds"]):
            f.text(mark, 571, f"{row['threshold']:.2f}", 14, anchor="center")
    f.dot(52, 632, BLUE, 6)
    f.text(67, 638, "Baseline", 16)
    f.dot(197, 632, GREEN, 6)
    f.text(212, 638, "With desktop samples", 16)
    f.text(45, 674, "The live station also requires eight consecutive stable frames. This sweep measures the score gate on individual images.", 15, color=MUTED)
    f.save()


def confusion(metrics):
    cohort = metrics["cohorts"]["public_candidate"]
    f = Figure("05_Public_Confusion_Matrices", "How the classifiers confused materials",
               f"Top-1 predictions on the same {cohort['baseline']['n']:,} public images · Cell labels are counts · Darker cells take a greater share of the true class")
    classes = tuple(s.title() for s in metrics["label_order"])
    for x, name, tag in ((44, "Dataset baseline", "baseline"), (652, "With desktop samples", "augmented")):
        f.box(x, 128, 584, 483, WHITE, EDGE)
        f.text(x+19, 166, name, 23, bold=True)
        f.text(x+348, 204, "Predicted class", 15, color=MUTED, anchor="center")
        f.text(x+19, 240, "True class", 14, bold=True, color=MUTED)
        left, cell_w = x+122, 73
        for column, label in enumerate(classes):
            f.text(left + (column+.5)*cell_w, 240, label, 13, bold=True, anchor="center")
        for row, values in enumerate(cohort[tag]["confusion"][:5]):
            y = 251+63*row
            support = sum(values)
            f.text(x+19, y+32, classes[row], 16, bold=True)
            for col, count in enumerate(values):
                xx = left+col*cell_w
                strength = count/support
                # A 0–70% share maps to the same light-to-dark scale in both matrices.
                t = min(1, strength/.70)
                r, g, b = (round(a+(z-a)*t) for a, z in zip((242, 247, 245), (26, 109, 92)))
                f.box(xx, y, cell_w-4, 56, f"#{r:02x}{g:02x}{b:02x}", radius=4)
                f.text(xx+(cell_w-4)/2, y+34, str(count), 16, bold=row==col,
                       color=WHITE if t > .58 else INK, anchor="center")
    f.text(44, 654, "Plastic → metal: 202 baseline, 199 augmented. Metal → plastic: 85 baseline, 78 augmented.", 16, bold=True)
    f.text(44, 680, "RealWaste candidate holdout. The public images cover broader material types than the desktop bottle and can use case.", 14, color=MUTED)
    f.save()


if __name__ == "__main__":
    architecture()
    wiring()
    metrics_path = OUT / "metrics.json"
    if metrics_path.is_file():
        results = json.loads(metrics_path.read_text(encoding="utf-8"))
        recall(results)
        threshold(results)
        confusion(results)
