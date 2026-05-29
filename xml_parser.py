"""
地籍図XML パーサー
法務省地籍図XMLフォーマット (http://www.moj.go.jp/MINJI/tizuxml) を解析する
"""
import xml.etree.ElementTree as ET
import zipfile
import io

NS_ZMN = 'http://www.moj.go.jp/MINJI/tizuzumen'
NS_MAP = 'http://www.moj.go.jp/MINJI/tizuxml'


def _t(ns, name):
    return f'{{{ns}}}{name}'


def parse_zip_or_xml(file_bytes: bytes) -> dict:
    """ZIPまたはXMLバイト列を受け取り、解析済みデータを返す。"""
    if file_bytes[:2] == b'PK':
        with zipfile.ZipFile(io.BytesIO(file_bytes)) as zf:
            xml_files = [n for n in zf.namelist() if n.lower().endswith('.xml')]
            if not xml_files:
                raise ValueError('ZIPファイル内にXMLファイルが見つかりません')
            with zf.open(xml_files[0]) as f:
                xml_bytes = f.read()
    else:
        xml_bytes = file_bytes

    return _parse_xml(xml_bytes)


def _parse_xml(xml_bytes: bytes) -> dict:
    """地籍図XMLバイト列を解析し、構造化データを返す。"""
    data = {
        'points': {},         # id -> (x, y)  ※公共座標系 X=北, Y=東
        'curves': {},         # id -> [(x, y), ...]
        'surfaces': {},       # id -> [curve_id, ...]
        'parcels': [],        # [{id, 大字名, 地番, 精度区分, surface_id}]
        'boundary_lines': [], # [{curve_id, 線種別}]
        'boundary_points': [],# [{点番名, point_id}]
        'ref_points': [],     # [{名称, point_id, 種別}]
        'metadata': {},
        'frames': [],         # [{地図番号, 縮尺分母, 左下座標, ...}]
    }

    context = ET.iterparse(io.BytesIO(xml_bytes), events=('end',))

    for _, elem in context:
        etag = elem.tag

        # ---- メタデータ ----
        if etag == _t(NS_MAP, '市区町村名'):
            data['metadata']['市区町村名'] = elem.text or ''
        elif etag == _t(NS_MAP, '地図名'):
            data['metadata']['地図名'] = elem.text or ''
        elif etag == _t(NS_MAP, '座標系'):
            data['metadata']['座標系'] = elem.text or ''

        # ---- 座標点 ----
        elif etag == _t(NS_ZMN, 'GM_Point'):
            pid = elem.get('id')
            x_el = elem.find(f'.//{_t(NS_ZMN, "X")}')
            y_el = elem.find(f'.//{_t(NS_ZMN, "Y")}')
            if pid and x_el is not None and y_el is not None:
                try:
                    data['points'][pid] = (float(x_el.text), float(y_el.text))
                except (ValueError, TypeError):
                    pass
            elem.clear()

        # ---- 曲線（線分列） ----
        elif etag == _t(NS_ZMN, 'GM_Curve'):
            cid = elem.get('id')
            coords = _extract_curve_coords(elem, data['points'])
            if cid and coords:
                data['curves'][cid] = coords
            elem.clear()

        # ---- 面 ----
        elif etag == _t(NS_ZMN, 'GM_Surface'):
            fid = elem.get('id')
            curve_ids = [
                g.get('idref')
                for g in elem.findall(f'.//{_t(NS_ZMN, "GM_CompositeCurve.generator")}')
                if g.get('idref')
            ]
            if fid:
                data['surfaces'][fid] = curve_ids
            elem.clear()

        # ---- 筆（土地区画） ----
        elif etag == _t(NS_MAP, '筆'):
            shape = elem.find(_t(NS_MAP, '形状'))
            data['parcels'].append({
                'id': elem.get('id'),
                '大字名': elem.findtext(_t(NS_MAP, '大字名')) or '',
                '地番': elem.findtext(_t(NS_MAP, '地番')) or '',
                '精度区分': elem.findtext(_t(NS_MAP, '精度区分')) or '',
                'surface_id': shape.get('idref') if shape is not None else None,
            })
            elem.clear()

        # ---- 筆界線 ----
        elif etag == _t(NS_MAP, '筆界線'):
            shape = elem.find(_t(NS_MAP, '形状'))
            data['boundary_lines'].append({
                'curve_id': shape.get('idref') if shape is not None else None,
                '線種別': elem.findtext(_t(NS_MAP, '線種別')) or '',
            })
            elem.clear()

        # ---- 筆界点 ----
        elif etag == _t(NS_MAP, '筆界点'):
            shape = elem.find(_t(NS_MAP, '形状'))
            data['boundary_points'].append({
                '点番名': elem.findtext(_t(NS_MAP, '点番名')) or '',
                'point_id': shape.get('idref') if shape is not None else None,
            })
            elem.clear()

        # ---- 基準点 ----
        elif etag == _t(NS_MAP, '基準点'):
            shape = elem.find(_t(NS_MAP, '形状'))
            data['ref_points'].append({
                '名称': elem.findtext(_t(NS_MAP, '名称')) or '',
                'point_id': shape.get('idref') if shape is not None else None,
                '種別': elem.findtext(_t(NS_MAP, '基準点種別')) or '',
            })
            elem.clear()

        # ---- 図郭 ----
        elif etag == _t(NS_MAP, '図郭'):
            frame = {
                '地図番号': elem.findtext(_t(NS_MAP, '地図番号')) or '',
                '縮尺分母': elem.findtext(_t(NS_MAP, '縮尺分母')) or '',
            }
            for corner in ('左下座標', '左上座標', '右下座標', '右上座標'):
                c_el = elem.find(_t(NS_MAP, corner))
                if c_el is not None:
                    x_el = c_el.find(_t(NS_ZMN, 'X'))
                    y_el = c_el.find(_t(NS_ZMN, 'Y'))
                    if x_el is not None and y_el is not None:
                        try:
                            frame[corner] = (float(x_el.text), float(y_el.text))
                        except (ValueError, TypeError):
                            pass
            data['frames'].append(frame)
            elem.clear()

    return data


def _extract_curve_coords(curve_elem, points_dict: dict) -> list:
    """GM_Curve要素から座標列を抽出する。直接座標と間接参照の両方に対応。"""
    coords = []
    for col in curve_elem.findall(f'.//{_t(NS_ZMN, "GM_PointArray.column")}'):
        direct = col.find(_t(NS_ZMN, 'GM_Position.direct'))
        if direct is not None:
            x_el = direct.find(_t(NS_ZMN, 'X'))
            y_el = direct.find(_t(NS_ZMN, 'Y'))
            if x_el is not None and y_el is not None:
                try:
                    coords.append((float(x_el.text), float(y_el.text)))
                except (ValueError, TypeError):
                    pass
        else:
            indirect = col.find(_t(NS_ZMN, 'GM_Position.indirect'))
            if indirect is not None:
                ref = indirect.find(_t(NS_ZMN, 'GM_PointRef.point'))
                if ref is not None:
                    idref = ref.get('idref')
                    if idref and idref in points_dict:
                        coords.append(points_dict[idref])
    return coords
