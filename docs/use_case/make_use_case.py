# Draws armbot_use_case.svg (UML use case diagram, notation from the Ch.9 Use Cases slides). Run: python3 make_use_case.py
import math
W,H=1500,960
F="DejaVu Sans, Arial, sans-serif"
out=[]
def t(x,y,s,size=15,anchor="middle",weight="normal",fill="#000",rot=0,italic=False,halo=False):
    tr=f' transform="rotate({rot:.1f} {x:.1f} {y:.1f})"' if rot else ""
    st=' font-style="italic"' if italic else ""
    h=' stroke="#fff" stroke-width="5" paint-order="stroke" stroke-linejoin="round"' if halo else ""
    s=s.replace("&","&amp;").replace("<","&lt;").replace(">","&gt;")
    out.append(f'<text x="{x:.1f}" y="{y:.1f}" font-family="{F}" font-size="{size}" text-anchor="{anchor}" font-weight="{weight}" fill="{fill}"{st}{tr}{h}>{s}</text>')
UC={}
def uc(key,label,x,y):
    rx=max(70,len(label)*4.7+22); ry=24
    UC[key]=(x,y,rx,ry)
    out.append(f'<ellipse cx="{x}" cy="{y}" rx="{rx:.1f}" ry="{ry}" fill="#fff" stroke="#000" stroke-width="1.4"/>')
    t(x,y+5.5,label,15.5)
ACT={}
def actor(key,label,x,y,sub=None):
    ACT[key]=(x,y)
    g=f'<g stroke="#000" stroke-width="1.6" fill="none"><circle cx="{x}" cy="{y-38}" r="12" fill="#fff"/><line x1="{x}" y1="{y-26}" x2="{x}" y2="{y+6}"/><line x1="{x-18}" y1="{y-14}" x2="{x+18}" y2="{y-14}"/><line x1="{x}" y1="{y+6}" x2="{x-15}" y2="{y+30}"/><line x1="{x}" y1="{y+6}" x2="{x+15}" y2="{y+30}"/></g>'
    out.append(g)
    t(x,y+52,label,16)
    if sub: t(x,y+70,sub,12.5,fill="#444",italic=True)
def edge_e(key,px,py):
    x,y,rx,ry=UC[key]; dx,dy=px-x,py-y
    k=1/math.sqrt((dx/rx)**2+(dy/ry)**2); return x+dx*k,y+dy*k
def assoc(a,u,label,frac=0.5,dy=-6):
    ax,ay=ACT[a]; ay-=6
    ex,ey=edge_e(u,ax,ay)
    sx=ax+(22 if ex>ax else -22)
    out.append(f'<line x1="{sx}" y1="{ay}" x2="{ex:.1f}" y2="{ey:.1f}" stroke="#000" stroke-width="1.2"/>')
    mx,my=sx+(ex-sx)*frac,ay+(ey-ay)*frac
    ang=math.degrees(math.atan2(ey-ay,ex-sx))
    if ang>90: ang-=180
    if ang<-90: ang+=180
    t(mx,my+dy,label,13,rot=ang,halo=True)
def dep(a,b,label,frac=0.5):
    ax,ay=UC[a][:2]; bx,by=UC[b][:2]
    sx,sy=edge_e(a,bx,by); ex,ey=edge_e(b,ax,ay)
    out.append(f'<line x1="{sx:.1f}" y1="{sy:.1f}" x2="{ex:.1f}" y2="{ey:.1f}" stroke="#000" stroke-width="1.2" stroke-dasharray="7 5" marker-end="url(#open)"/>')
    mx,my=sx+(ex-sx)*frac,sy+(ey-sy)*frac
    ang=math.degrees(math.atan2(ey-sy,ex-sx))
    if ang>90: ang-=180
    if ang<-90: ang+=180
    t(mx,my-7,label,13,rot=ang,halo=True)

out.append(f'<rect width="{W}" height="{H}" fill="#fff"/>')
out.append('<defs><marker id="open" viewBox="0 0 12 12" refX="11" refY="6" markerWidth="13" markerHeight="13" orient="auto"><path d="M1 1 L11 6 L1 11" fill="none" stroke="#000" stroke-width="1.3"/></marker></defs>')
t(W/2,44,"Use Case Diagram — ARMbot Pick and Place",26,weight="bold")
# boundary
bx0,by0,bx1,by1=330,90,1110,905
out.append(f'<rect x="{bx0}" y="{by0}" width="{bx1-bx0}" height="{by1-by0}" fill="none" stroke="#000" stroke-width="1.5"/>')
t((bx0+bx1)/2,118,"ARMbot System",17,weight="bold")

# use cases
uc("inv","UC1: ViewInventory",540,205)
uc("pick","UC2: PickItem",540,330)
uc("queue","UC3: ViewJobStatus",540,455)
uc("cancel","UC4: CancelJob",540,570)
uc("stop","UC5: EmergencyStop",540,680)
uc("view","UC6: ViewInspectionResult",540,800)
uc("unk","UC7: ReportUnknownObject",910,215)
uc("rest","UC8: ReturnToRest",910,620)
uc("submit","UC9: SubmitInspectionResult",910,800)
# actors
actor("op","Operator",150,500,"«initiating actor»")
actor("cam","Camera",1340,200,"(ESP32-CAM)")
actor("arm","RobotArm",1340,430,"(PCA9685 + servos)")
actor("dash","InspectionDashboard",1340,700,"(receiving station, via MQTT)")
for u,f in (("inv",.5),("pick",.5),("queue",.5),("cancel",.5),("stop",.5),("view",.5)):
    assoc("op",u,"«initiate»",f)
assoc("cam","pick","«participate»",.30)
assoc("arm","pick","«participate»",.22)
assoc("arm","rest","«participate»",.35)
assoc("dash","pick","«initiate»",.16)
assoc("dash","unk","«participate»",.12)
assoc("dash","submit","«initiate»",.45)
dep("pick","rest","«include»",.5)
dep("stop","rest","«include»",.5)
dep("unk","pick","«extend»",.5)
# extension point note on PickItem
t(540,372,"extension point: object has no class",11.5,fill="#444",italic=True)
# legend
lx,ly=30,95
out.append(f'<rect x="{lx}" y="{ly}" width="270" height="250" fill="#fff" stroke="#16a34a" stroke-width="3"/>')
for i,s in enumerate(["UC1: ViewInventory","UC2: PickItem","UC3: ViewJobStatus","UC4: CancelJob","UC5: EmergencyStop","UC6: ViewInspectionResult","UC7: ReportUnknownObject","UC8: ReturnToRest","UC9: SubmitInspectionResult"]):
    t(lx+14,ly+28+i*25.5,s,14.5,anchor="start")
open("armbot_use_case.svg","w").write(f'<svg xmlns="http://www.w3.org/2000/svg" width="{W}" height="{H}" viewBox="0 0 {W} {H}">'+"".join(out)+"</svg>")
