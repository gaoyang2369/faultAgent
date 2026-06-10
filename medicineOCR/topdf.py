import os

# 1. 必须在引入 weasyprint 之前配置环境
gtk_path = r'd:/GTK3-Runtime Win64/bin'#需配置GTK运行时路径
if os.path.exists(gtk_path):
    if hasattr(os, 'add_dll_directory'):
        os.add_dll_directory(gtk_path)
    os.environ['PATH'] = gtk_path + os.pathsep + os.environ['PATH']

import json
import re
import markdown
from weasyprint import HTML, CSS

class PDFGenerator:
    def convert_to_pdf_smart_anchor(self, mmd_file, json_metadata, pdf_output, crops_dir):
        # 2. 读取并排序签名数据
        if not os.path.exists(json_metadata):
            signatures = []
        else:
            with open(json_metadata, 'r', encoding='utf-8') as f:
                meta = json.load(f)
            signatures = sorted(meta['signatures'], key=lambda x: x['y'])

        # 3. 处理 Markdown 文本，注入签名图片
        with open(mmd_file, 'r', encoding='utf-8') as f:
            mmd_text = f.read()

        sig_index = 0
        def inject_signature(match):
            nonlocal sig_index
            original_text = match.group(0)
            if sig_index < len(signatures):
                sig = signatures[sig_index]
                sig_path = os.path.join(crops_dir, sig['crop_file']).replace('\\', '/')
                img_url = f"file:///{sig_path}"
                replacement = f'{original_text} <img src="{img_url}" style="height: 3em; width: auto; vertical-align: middle; margin-left: 5px;">'
                sig_index += 1
                return replacement
            return original_text

        new_mmd_text = re.sub(r'检查者[:：]', inject_signature, mmd_text)

        # 4. 转换为 HTML
        html_body = markdown.markdown(new_mmd_text, extensions=['tables', 'fenced_code'])

        # 5. 定义样式 (保持原样)
        css_string = """
        @page { size: A4; margin: 2cm; }
        body { font-family: "Microsoft YaHei", "SimSun", sans-serif; line-height: 1.8; color: #333; }
        h1, h2, h3 { color: #2c3e50; border-bottom: 2px solid #eee; padding-bottom: 10px; margin-top: 20px; font-weight: bold; }
        h2 { font-size: 1.5em; }
        table { width: 100%; border-collapse: collapse; margin: 20px 0; font-size: 0.9em; }
        th, td { border: 1px solid #ddd; padding: 10px; text-align: left; }
        tr:nth-child(even) { background-color: #f9f9f9; }
        th, tr:first-child td { background-color: #f2f2f2; font-weight: bold; }
        """

        full_html = f"<html><head><meta charset='utf-8'></head><body>{html_body}</body></html>"
        
        # 6. 执行转换
        HTML(string=full_html).write_pdf(
            pdf_output,
            stylesheets=[CSS(string=css_string)]
        )
        
        return pdf_output