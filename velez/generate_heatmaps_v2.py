"""
generate_heatmaps_v2.py — Bootstrap: generate 12 heatmaps + update config_velez.json
Run once locally to seed the initial state. Railway takes over after that.
Usage: python generate_heatmaps_v2.py
"""
import sys, os, json, hashlib
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "railway-backend"))

import numpy as np
import matplotlib
matplotlib.use("Agg")
import matplotlib.patches as mpatches
import matplotlib.pyplot as plt
from matplotlib.colors import LinearSegmentedColormap
from matplotlib.figure import Figure
from matplotlib.backends.backend_agg import FigureCanvasAgg
from scipy.ndimage import zoom as _zoom, gaussian_filter
from PIL import Image

try:
    import qrcode
    HAS_QR = True
except ImportError:
    HAS_QR = False

SCRIPT_DIR = Path(__file__).parent
OUT_DIR    = SCRIPT_DIR / "heatmaps"
CFG_FILE   = SCRIPT_DIR / "config_velez.json"
OUT_DIR.mkdir(exist_ok=True)

W_PX, H_PX, DPI = 800, 520, 100
QR_URL = "https://protocolfaro.github.io/faroprotocol/velez/verify"
BG = "#0d1117"
WHITE = "#ffffff"
SEM_COL = {"verde":"#27ae60","amarillo":"#f0b429","rojo":"#e74c3c"}

DIMS = {
    "1fa":(105,68),"2fa":(105,68),"3fa":(105,68),"4fa":(105,68),
    "5fa":(105,70),"6fa":(105,70),"7fa":(105,70),"8fa":(105,70),
    "9fa":(105,70),"10fa":(105,70),"1fp":(105,68),"2fp":(105,68),
}

SEMANA_C_IPOS = {
    "1fa":  {"score":1845.0,"semaforo":"rojo","icono":"🔴","texto":"Cancha al límite — necesita descanso","personas":585,"horas":74.0,"detalle":"Esta semana pisaron esta cancha 585 personas durante 74 horas en total"},
    "3fa":  {"score":180.0, "semaforo":"amarillo","icono":"🟡","texto":"Uso moderado — seguir de cerca","personas":60,"horas":6.0,"detalle":"Esta semana pisaron esta cancha 60 personas durante 6 horas en total"},
    "5fa":  {"score":298.5,"semaforo":"rojo","icono":"🔴","texto":"Uso muy alto — monitorear de cerca","personas":105,"horas":23.0,"detalle":"Esta semana pisaron esta cancha 105 personas durante 23 horas en total"},
    "8fa":  {"score":676.5,"semaforo":"rojo","icono":"🔴","texto":"Cancha al límite — necesita descanso","personas":240,"horas":33.0,"detalle":"Esta semana pisaron esta cancha 240 personas durante 33 horas en total"},
    "9fa":  {"score":676.5,"semaforo":"rojo","icono":"🔴","texto":"Cancha al límite — necesita descanso","personas":240,"horas":33.0,"detalle":"Esta semana pisaron esta cancha 240 personas durante 33 horas en total"},
    "2fa":  {"score":0,"semaforo":"verde","icono":"🟢","texto":"Cancha descansada","personas":0,"horas":0,"detalle":"Esta semana no se usó esta cancha"},
    "4fa":  {"score":0,"semaforo":"verde","icono":"🟢","texto":"Cancha descansada","personas":0,"horas":0,"detalle":"Esta semana no se usó esta cancha"},
    "6fa":  {"score":0,"semaforo":"verde","icono":"🟢","texto":"Cancha descansada","personas":0,"horas":0,"detalle":"Esta semana no se usó esta cancha"},
    "7fa":  {"score":0,"semaforo":"verde","icono":"🟢","texto":"Cancha descansada","personas":0,"horas":0,"detalle":"Esta semana no se usó esta cancha"},
    "10fa": {"score":0,"semaforo":"verde","icono":"🟢","texto":"Cancha descansada","personas":0,"horas":0,"detalle":"Esta semana no se usó esta cancha"},
    "1fp":  {"score":0,"semaforo":"verde","icono":"🟢","texto":"Cancha descansada","personas":0,"horas":0,"detalle":"Cancha de primer equipo — sin carga registrada esta semana"},
    "2fp":  {"score":0,"semaforo":"verde","icono":"🟢","texto":"Cancha descansada","personas":0,"horas":0,"detalle":"Cancha de primer equipo — sin carga registrada esta semana"},
}

_CMAP = LinearSegmentedColormap.from_list("ipos_ndvi",[
    (0.00,"#b71c1c"),(0.22,"#e05252"),(0.40,"#e0b84c"),
    (0.60,"#8bc34a"),(0.80,"#4cad72"),(1.00,"#1b5e20"),
])

def _ndvi_grid(rows,cols,base,seed=42):
    rng=np.random.default_rng(seed)
    g=np.full((rows,cols),base,dtype=np.float32)
    g+=0.012*np.sin(np.arange(cols)*np.pi/1.4)
    yr=np.minimum(np.arange(rows),rows-1-np.arange(rows))/max(1,rows*0.18)
    xc=np.minimum(np.arange(cols),cols-1-np.arange(cols))/max(1,cols*0.18)
    edge=np.clip(np.outer(yr,np.ones(cols))*np.outer(np.ones(rows),xc),0,1)
    g-=0.04*(1-edge)
    g+=rng.normal(0,0.015,(rows,cols)).astype(np.float32)
    return np.clip(g,0.0,1.0)

def _usage(rows,cols,ipos):
    Y,X=np.mgrid[0:rows,0:cols].astype(np.float32)
    spots=[(0.50,0.50,0.18,1.0),(0.50,0.11,0.10,0.75),(0.50,0.89,0.10,0.75),
           (0.50,0.05,0.06,0.50),(0.50,0.95,0.06,0.50)]
    m=np.zeros((rows,cols),np.float32)
    for rf,cf,rad_f,w in spots:
        cr,cc=rf*rows,cf*cols; rr=max(1.0,rad_f*min(rows,cols))
        m+=w*np.exp(-((Y-cr)**2+(X-cc)**2)/(2*(rr*0.45)**2))
    m=np.clip(m/m.max(),0,1)
    return m*0.40*min(1.0,ipos/350.0)

def _lines(ax,W,H):
    kw=dict(color=WHITE,lw=1.3,alpha=0.72)
    ax.plot([0,W,W,0,0],[0,0,H,H,0],**kw)
    ax.plot([W/2,W/2],[0,H],**kw)
    ax.add_patch(mpatches.Circle((W/2,H/2),9.15,fill=False,**kw))
    ax.plot(W/2,H/2,"o",color=WHITE,ms=2.5,alpha=0.72)
    for x0 in [0,W-16.5]:
        ax.add_patch(mpatches.Rectangle((x0,(H-40.32)/2),16.5,40.32,fill=False,**kw))
    for x0 in [0,W-5.5]:
        ax.add_patch(mpatches.Rectangle((x0,(H-18.32)/2),5.5,18.32,fill=False,**kw))
    for xp in [11.0,W-11.0]:
        ax.plot(xp,H/2,"o",color=WHITE,ms=2.5,alpha=0.72)
    for cx,t1,t2 in [(11.0,-53,53),(W-11.0,127,233)]:
        ax.add_patch(mpatches.Arc((cx,H/2),18.3,18.3,theta1=t1,theta2=t2,**kw))
    for cx,cy,t1,t2 in [(0,0,0,90),(W,0,90,180),(0,H,270,360),(W,H,180,270)]:
        ax.add_patch(mpatches.Arc((cx,cy),2,2,theta1=t1,theta2=t2,**kw))

def _qr_arr():
    if not HAS_QR: return None
    try:
        qr=qrcode.QRCode(version=2,box_size=3,border=1)
        qr.add_data(QR_URL); qr.make(fit=True)
        img=qr.make_image(fill_color="white",back_color="#0d1117")
        return np.array(img.convert("RGBA"))
    except Exception: return None

def make_heatmap(cid, data, semana_label, ts, qr=None):
    if cid not in DIMS: return None
    W_m,H_m=DIMS[cid]
    ipos=data["score"]; sem=data["semaforo"]
    col=SEM_COL.get(sem,SEM_COL["verde"])
    label=cid.upper(); txt=data["texto"]

    ndvi_base=max(0.18, 0.72-(ipos/350.0)*0.54)
    sha_hex=hashlib.sha256(f"{cid}|{ndvi_base:.4f}|{ipos:.2f}|{semana_label}|{ts}".encode()).hexdigest()

    rows=max(4,round(H_m/10)+1); cols=max(5,round(W_m/10)+1)
    seed=abs(hash(cid))%(2**31)
    ndvi_g=_ndvi_grid(rows,cols,ndvi_base,seed)
    use_g=_usage(rows,cols,ipos)

    fig=Figure(figsize=(W_PX/DPI,H_PX/DPI),dpi=DPI)
    fig.patch.set_facecolor(BG)
    canvas=FigureCanvasAgg(fig)
    ax=fig.add_axes([0.0,0.07,0.92,0.93]); ax.set_facecolor(BG)

    sr=H_PX*0.93/rows; sc=W_PX*0.92/cols
    ndvi_up=_zoom(ndvi_g.astype(np.float64),(sr,sc),order=3)
    ndvi_up=gaussian_filter(ndvi_up,sigma=1.5)
    ax.imshow(ndvi_up,cmap=_CMAP,vmin=0,vmax=1,origin="upper",
              aspect="auto",extent=[0,W_m,H_m,0])
    ax.set_xlim(0,W_m); ax.set_ylim(H_m,0)

    use_up=_zoom(use_g.astype(np.float64),(sr,sc),order=1)
    use_up=gaussian_filter(use_up,sigma=2.2)
    ax.imshow(use_up,cmap="Greys_r",vmin=0,vmax=1,alpha=0.40,
              origin="upper",aspect="auto",extent=[0,W_m,H_m,0])

    _lines(ax,W_m,H_m); ax.axis("off")

    ax.text(0.984,0.974,f"  {label}",transform=ax.transAxes,fontsize=13,
            fontweight="bold",color=col,ha="right",va="top",
            bbox=dict(boxstyle="round,pad=0.3",facecolor="#000000CC",
                      edgecolor=col,linewidth=1.8))
    ax.text(0.984,0.895,txt,transform=ax.transAxes,fontsize=7,
            fontweight="bold",color=col,ha="right",va="top",
            bbox=dict(boxstyle="round,pad=0.22",facecolor="#000000CC",
                      edgecolor=col+"88",linewidth=0.9))

    if qr is not None:
        try:
            ax_qr=ax.inset_axes([0.865,0.02,0.125,0.20])
            ax_qr.imshow(qr); ax_qr.axis("off")
        except Exception: pass

    ax_f=fig.add_axes([0.0,0.0,1.0,0.07]); ax_f.set_facecolor("#040404"); ax_f.axis("off")
    p=data.get("personas",0); h=data.get("horas",0)
    ax_f.text(0.005,0.78,f"IPOS {ipos:.0f}  ·  {p} personas  ·  {h:.0f}h  ·  Semana {semana_label}",
              transform=ax_f.transAxes,fontsize=7,fontweight="bold",color=col,
              va="top",ha="left",fontfamily="monospace")
    ax_f.text(0.005,0.18,f"SHA-256: {sha_hex[:48]}",
              transform=ax_f.transAxes,fontsize=5.0,color="#ffffff55",
              va="top",ha="left",fontfamily="monospace")
    ax_f.text(0.88,0.78,"Faro Protocol",transform=ax_f.transAxes,
              fontsize=5.5,color="#c9a84c88",va="top",ha="right",fontfamily="monospace")

    cbar=fig.add_axes([0.926,0.07,0.028,0.93]); cbar.set_facecolor(BG)
    cb=np.linspace(1,0,200).reshape(200,1)
    cbar.imshow(cb,cmap=_CMAP,aspect="auto",vmin=0,vmax=1)
    cbar.set_xticks([]); cbar.set_yticks([0,66,133,199])
    cbar.set_yticklabels(["Óptimo","Bueno","Estrés","Crítico"],fontsize=5.5,color="#ffffffcc")
    for sp in cbar.spines.values(): sp.set_visible(False)
    cbar.tick_params(length=0)

    canvas.draw()
    raw=canvas.buffer_rgba()
    pil=Image.frombytes("RGBA",canvas.get_width_height(),raw).convert("RGB")
    out_path=OUT_DIR/f"heatmap_{cid}.png"
    pil.save(out_path,"PNG",optimize=True)
    plt.close(fig)
    return sha_hex

def main():
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    semana_label = "C"
    qr = _qr_arr()
    verify_hashes = {}

    print(f"Generando 12 heatmaps → {OUT_DIR}")
    for cid, data in SEMANA_C_IPOS.items():
        sha_hex = make_heatmap(cid, data, semana_label, ts, qr)
        if sha_hex:
            verify_hashes[sha_hex] = {
                "cancha": cid.upper(),
                "semana": semana_label,
                "ipos": data["score"],
                "ndvi": round(max(0.18, 0.72-(data["score"]/350.0)*0.54), 4),
                "semaforo": data["semaforo"],
                "fecha_generacion": ts,
            }
        print(f"  ✓ heatmap_{cid}.png  IPOS={data['score']}  {data['icono']} {data['semaforo']}")

    # Update config_velez.json
    if CFG_FILE.exists():
        with open(CFG_FILE, encoding="utf-8") as f:
            cfg = json.load(f)
    else:
        cfg = {}

    cfg["ipos_semana"] = {
        "label": semana_label,
        "fecha_inicio": "2026-05-18",
        "fecha_fin": "2026-05-23",
        "generado_en": ts,
        "canchas": SEMANA_C_IPOS,
    }
    cfg["verify_hashes"] = verify_hashes

    with open(CFG_FILE, "w", encoding="utf-8") as f:
        json.dump(cfg, f, ensure_ascii=False, indent=2)

    print(f"\n✓ config_velez.json actualizado ({len(verify_hashes)} hashes)")
    print("\nListo. Próximos pasos:")
    print("  1. git add velez/heatmaps/*.png velez/config_velez.json")
    print("  2. git commit -m 'heatmaps semana C — IPOS v2'")
    print("  3. git push origin main")

if __name__ == "__main__":
    main()
