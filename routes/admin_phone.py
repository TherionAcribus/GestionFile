from flask import Blueprint, render_template, current_app as app

admin_phone_bp = Blueprint('admin_phone', __name__)

@admin_phone_bp.route('/admin/phone')
def admin_phone():
    phone_lines = []
    for line in range(1, 7):
        exec(f"phone_line{line} = app.config['PHONE_LINE{line}']"),
        phone_lines.append(eval(f"phone_line{line}"))
    print("PL", phone_lines)
    print("CENTER", app.config['PHONE_CENTER'])
    return render_template('/admin/phone.html',
                            phone_center = app.config['PHONE_CENTER'], 
                            phone_title=app.config['PHONE_TITLE'],
                            phone_lines=phone_lines,
                            phone_display_specific_message=app.config['PHONE_DISPLAY_SPECIFIC_MESSAGE'])
                            
