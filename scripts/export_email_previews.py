"""Export sample-only email previews without sending mail or using the production DB.
Run: .venv/bin/python scripts/export_email_previews.py
"""
import sys
import os
import tempfile
from html import escape
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
preview_database = tempfile.TemporaryDirectory(prefix='located-email-preview-')
os.environ['DATABASE_URL'] = ''
os.environ['DATABASE_PATH'] = str(Path(preview_database.name) / 'preview.sqlite3')
from app import app

output=Path(__file__).resolve().parents[1]/'previews/emails'
output.mkdir(parents=True,exist_ok=True)
with app.test_request_context('/'):
    # Fixed demonstration values keep exports independent of deployment settings.
    app.config.update(PUBLIC_BASE_URL='https://example.com',MAIL_REPLY_TO='support@example.com')
    examples=app.extensions['email_samples']()
    for event,message in examples.items():
        (output/f'{event}.html').write_text(message['html'])
    links=''.join(f'<a href="{event}.html" target="email-preview" data-preview><span>{index:02d}</span>{escape(message["subject"])}</a>' for index,(event,message) in enumerate(examples.items(),1))
    (output/'index.html').write_text('''<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Locatediscount · Email previews</title><style>
*{box-sizing:border-box}body{margin:0;background:#f4f5f7;color:#152b3d;font-family:Arial,Helvetica,sans-serif}header{background:white;border-bottom:1px solid #dce2e7;padding:24px 32px}header h1{font-size:23px;letter-spacing:-.6px;margin:0}header p{font-size:13px;line-height:1.6;color:#657582;margin:8px 0 0}.layout{display:grid;grid-template-columns:300px 1fr;min-height:calc(100vh - 109px)}nav{background:white;padding:20px;border-right:1px solid #dce2e7}nav a{display:flex;gap:12px;padding:14px 12px;border-bottom:1px solid #e9edf0;font-size:13px;line-height:1.5;text-decoration:none;color:#173774}nav a:hover,nav a.active{background:#edf1f7}nav span{color:#748492}main{padding:24px;min-width:0}.toolbar{display:flex;justify-content:center;gap:8px;margin-bottom:20px}button{border:1px solid #bac8d4;border-radius:6px;background:white;color:#173774;font-size:13px;font-weight:600;padding:9px 16px;cursor:pointer}button[aria-pressed=true]{background:#173774;color:white}iframe{display:block;max-width:100%;width:760px;height:850px;margin:0 auto;border:1px solid #dce2e7;background:#f3f4f6}small{display:block;text-align:center;color:#657582;margin:14px;font-size:12px}@media(max-width:700px){header{padding:20px}.layout{grid-template-columns:1fr}nav{max-height:210px;overflow:auto;border-bottom:1px solid #dce2e7}main{padding:16px}iframe{height:850px}}
</style></head><body><header><h1>Locatediscount / Email previews</h1><p>13 transactional email designs · Sample data only · No emails are sent from this page.</p></header><div class="layout"><nav>'''+links+'''</nav><main><div class="toolbar"><button data-width="760" aria-pressed="true">Desktop</button><button data-width="375" aria-pressed="false">Mobile</button></div><iframe name="email-preview" src="verification.html" title="Email design preview"></iframe><small>Buttons and addresses use demonstration links. Actual delivery uses your configured website and support address.</small></main></div><script>document.querySelectorAll('[data-width]').forEach(button=>button.addEventListener('click',()=>{document.querySelector('iframe').style.width=button.dataset.width+'px';document.querySelectorAll('[data-width]').forEach(item=>item.setAttribute('aria-pressed',String(item===button)))}));document.querySelectorAll('[data-preview]').forEach(link=>link.addEventListener('click',()=>{document.querySelectorAll('[data-preview]').forEach(item=>item.classList.toggle('active',item===link))}));document.querySelector('[data-preview]').classList.add('active');</script></body></html>''')
print(f'Exported {len(examples)} email previews: {output / "index.html"}')

preview_database.cleanup()
