"""
地籍図XML → DXF 変換 Webアプリ
"""
import io
import os
import traceback
from flask import Flask, request, jsonify, send_file, render_template

app = Flask(__name__)
app.config['MAX_CONTENT_LENGTH'] = 150 * 1024 * 1024  # 150MB


@app.route('/')
def index():
    return render_template('index.html')


@app.route('/convert', methods=['POST'])
def convert():
    if 'file' not in request.files:
        return jsonify({'error': 'ファイルが選択されていません'}), 400

    uploaded = request.files['file']
    if not uploaded.filename:
        return jsonify({'error': 'ファイル名が空です'}), 400

    fname_lower = uploaded.filename.lower()
    if not (fname_lower.endswith('.xml') or fname_lower.endswith('.zip')):
        return jsonify({'error': 'XMLまたはZIPファイルを選択してください'}), 400

    try:
        from xml_parser import parse_zip_or_xml
        from dxf_writer import create_dxf

        file_bytes = uploaded.read()

        data = parse_zip_or_xml(file_bytes)

        stats = {
            'parcels': len(data['parcels']),
            'curves': len(data['curves']),
            'points': len(data['points']),
        }

        dxf_bytes = create_dxf(data)

        base = os.path.splitext(uploaded.filename)[0]
        dxf_filename = f"{base}.dxf"

        return send_file(
            io.BytesIO(dxf_bytes),
            mimetype='application/dxf',
            as_attachment=True,
            download_name=dxf_filename,
        )

    except Exception as e:
        traceback.print_exc()
        return jsonify({'error': str(e)}), 500


if __name__ == '__main__':
    app.run(debug=True, host='0.0.0.0', port=5000)
