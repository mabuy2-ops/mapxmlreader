"""
地籍図データ → DXF 変換器
公共座標系 (X=北, Y=東) → DXF座標 (X=東, Y=北) に変換して出力する

JW-CAD互換のためLINEエンティティを使用（LWPOLYLINEは不可）
レイヤー名はShift-JIS互換のためASCII英字を使用
"""
import io
import ezdxf
from ezdxf.enums import TextEntityAlignment


# DXFレイヤー定義: (ASCII名, 表示用日本語名, ACI色番号)
# JW-CADはUTF-8の日本語レイヤー名を正しく扱えないためASCII名を使用
LAYERS = [
    ('BOUNDARY',   '筆界線', 1),  # 赤
    ('LANDNUMBER', '地番',   3),  # 緑
    ('BND_POINT',  '筆界点', 5),  # 青
    ('REF_POINT',  '基準点', 2),  # 黄
    ('FRAME',      '図郭',   7),  # 白
]


def _dxf_xy(jis_x: float, jis_y: float):
    """公共座標 (X=北, Y=東) → DXF (X=東, Y=北) かつ m→mm 変換。"""
    return (jis_y * 1000, jis_x * 1000)


def _centroid(points: list):
    if not points:
        return None
    xs = [p[0] for p in points]
    ys = [p[1] for p in points]
    return (sum(xs) / len(xs), sum(ys) / len(ys))


def _add_polyline(msp, pts_2d: list, layer: str):
    """座標列をLINEエンティティの列として追加（JW-CAD互換）。"""
    for i in range(len(pts_2d) - 1):
        msp.add_line(pts_2d[i], pts_2d[i + 1], dxfattribs={'layer': layer})


def _add_closed_polyline(msp, pts_2d: list, layer: str):
    """閉じたポリラインをLINEエンティティの列として追加。"""
    if len(pts_2d) < 2:
        return
    _add_polyline(msp, pts_2d, layer)
    msp.add_line(pts_2d[-1], pts_2d[0], dxfattribs={'layer': layer})


def create_dxf(data: dict) -> bytes:
    """解析済みデータからDXFバイト列を生成する。"""
    doc = ezdxf.new('R12')
    msp = doc.modelspace()

    for ascii_name, _, color in LAYERS:
        doc.layers.add(name=ascii_name, color=color)

    pts_dict = data['points']
    curves   = data['curves']
    surfaces = data['surfaces']

    # 縮尺からテキスト高さを算出 (モデル空間単位 = mm)
    scale = 500
    if data['frames']:
        try:
            scale = int(data['frames'][0].get('縮尺分母', 500))
        except (ValueError, TypeError):
            pass
    text_height = scale * 3  # 3mm @ 印刷スケール（mm単位）

    # 全曲線座標からDXF座標範囲を算出（m→mm変換後）
    raw_pts = [_dxf_xy(x, y) for curve in curves.values() for x, y in curve]
    if raw_pts:
        xs = [p[0] for p in raw_pts]
        ys = [p[1] for p in raw_pts]
        mg  = max(max(xs) - min(xs), max(ys) - min(ys)) * 0.02
        # JW-CADはLIMMINを原点として扱うため、左下が原点付近になるようオフセット
        ox  = min(xs) - mg
        oy  = min(ys) - mg
        xmx = max(xs) - min(xs) + 2 * mg
        ymx = max(ys) - min(ys) + 2 * mg
    else:
        mg = 1000; ox = 0.0; oy = 0.0; xmx = 1000.0; ymx = 1000.0

    def dxy(jis_x: float, jis_y: float):
        """公共座標(X=北,Y=東)→DXF(X=東,Y=北) m→mm＋原点オフセット"""
        rx, ry = _dxf_xy(jis_x, jis_y)
        return (rx - ox, ry - oy)

    # ---- 筆界線 → LINEエンティティ ----
    for bl in data['boundary_lines']:
        cid = bl.get('curve_id')
        if cid and cid in curves:
            pts = curves[cid]
            if len(pts) >= 2:
                dxf_pts = [dxy(x, y) for x, y in pts]
                _add_polyline(msp, dxf_pts, 'BOUNDARY')

    # ---- 筆 → 地番テキスト（重心位置） ----
    for parcel in data['parcels']:
        sid    = parcel.get('surface_id')
        chiban = parcel.get('地番', '').strip()

        if sid and sid in surfaces and chiban:
            all_pts = []
            for cid in surfaces[sid]:
                if cid in curves:
                    all_pts.extend(curves[cid])

            centroid = _centroid(all_pts)
            if centroid:
                cx, cy = dxy(centroid[0], centroid[1])
                t = msp.add_text(
                    chiban,
                    dxfattribs={'layer': 'LANDNUMBER', 'height': text_height},
                )
                t.set_placement((cx, cy), align=TextEntityAlignment.MIDDLE_CENTER)

    # ---- 筆界点 → POINTエンティティ ----
    for bp in data['boundary_points']:
        pid = bp.get('point_id')
        if pid and pid in pts_dict:
            x, y = pts_dict[pid]
            msp.add_point(dxy(x, y), dxfattribs={'layer': 'BND_POINT'})

    # ---- 基準点 → POINT + テキスト ----
    for rp in data['ref_points']:
        pid = rp.get('point_id')
        if pid and pid in pts_dict:
            x, y = pts_dict[pid]
            dx, dy = dxy(x, y)
            msp.add_point((dx, dy), dxfattribs={'layer': 'REF_POINT'})
            name = rp.get('名称', '').strip()
            if name:
                t = msp.add_text(
                    name,
                    dxfattribs={'layer': 'REF_POINT', 'height': text_height * 0.8},
                )
                t.set_placement(
                    (dx + text_height, dy + text_height),
                    align=TextEntityAlignment.BOTTOM_LEFT,
                )

    # ---- 図郭枠 → 閉じたLINEエンティティ ----
    for frame in data['frames']:
        corners = []
        for cname in ('左下座標', '右下座標', '右上座標', '左上座標'):
            if cname in frame:
                x, y = frame[cname]
                corners.append(dxy(x, y))
        if len(corners) == 4:
            _add_closed_polyline(msp, corners, 'FRAME')

            map_no = frame.get('地図番号', '').strip()
            if map_no:
                cx = sum(c[0] for c in corners) / 4
                cy = sum(c[1] for c in corners) / 4
                t = msp.add_text(
                    map_no,
                    dxfattribs={'layer': 'FRAME', 'height': text_height * 1.2},
                )
                t.set_placement((cx, cy), align=TextEntityAlignment.MIDDLE_CENTER)

    # 図面範囲を設定（JW-CADの初期表示位置決定に必要）
    if raw_pts:
        vps = doc.viewports.get_config('*Active')
        if vps:
            vp = vps[0]
            vp.dxf.center = (xmx / 2, ymx / 2)
            vp.dxf.height = ymx * 1.15

    buf = io.StringIO()
    doc.write(buf)
    dxf = buf.getvalue()

    # ezdxf が無視する $EXTMIN/$EXTMAX/$LIMMAX を後処理で書き換え
    # R12形式では$INSUNITSは存在しないため不要
    # $LIMMINはオフセット後(0,0)のためezdxfデフォルト値と一致・変更不要
    if raw_pts:
        dxf = dxf.replace(
            '$EXTMIN\n 10\n1e+20\n 20\n1e+20\n 30\n1e+20\n',
            f'$EXTMIN\n 10\n0.000\n 20\n0.000\n 30\n0.000\n',
        )
        dxf = dxf.replace(
            '$EXTMAX\n 10\n-1e+20\n 20\n-1e+20\n 30\n-1e+20\n',
            f'$EXTMAX\n 10\n{xmx:.3f}\n 20\n{ymx:.3f}\n 30\n0.000\n',
        )
        dxf = dxf.replace(
            '$LIMMAX\n 10\n420.0\n 20\n297.0\n',
            f'$LIMMAX\n 10\n{xmx:.3f}\n 20\n{ymx:.3f}\n',
        )

    return dxf.encode('utf-8')
