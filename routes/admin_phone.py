from flask import Blueprint, render_template, current_app as app
from routes.admin_security import require_permission

admin_phone_bp = Blueprint('admin_phone', __name__)

@admin_phone_bp.route('/admin/phone')
@require_permission('phone')
def admin_phone():

    phone_lines = {
        f"phone_line{i}": {
            'text': app.config[f'PHONE_LINE{i}'],
            'font_size': app.css_variable_manager.get_variable('phone', f'phone_line{i}_font_size'),
            'font_weight': app.css_variable_manager.get_variable('phone', f'phone_line{i}_font_weight'),
            'font_color': app.css_variable_manager.get_variable('phone', f'phone_line{i}_font_color'),
            'border_size': app.css_variable_manager.get_variable('phone', f'phone_line{i}_border_size'),
            'border_color': app.css_variable_manager.get_variable('phone', f'phone_line{i}_border_color'),
            'background_height': app.css_variable_manager.get_variable('phone', f'phone_line{i}_background_height'),
            'background_color': app.css_variable_manager.get_variable('phone', f'phone_line{i}_background_color'),
        }
        for i in range(1, 7)
    }
    
    phone_your_turn_lines = {
        f"phone_your_turn_line{i}": {
            'text': app.config[f'PHONE_YOUR_TURN_LINE{i}'],
            'font_size': app.css_variable_manager.get_variable('phone', f'phone_your_turn_line{i}_font_size'),
            'font_weight': app.css_variable_manager.get_variable('phone', f'phone_your_turn_line{i}_font_weight'),
            'font_color': app.css_variable_manager.get_variable('phone', f'phone_your_turn_line{i}_font_color'),
            'border_size': app.css_variable_manager.get_variable('phone', f'phone_your_turn_line{i}_border_size'),
            'border_color': app.css_variable_manager.get_variable('phone', f'phone_your_turn_line{i}_border_color'),
            'background_height': app.css_variable_manager.get_variable('phone', f'phone_your_turn_line{i}_background_height'),
            'background_color': app.css_variable_manager.get_variable('phone', f'phone_your_turn_line{i}_background_color'),
        }
        for i in range(1, 7)
    }

    return render_template('/admin/phone.html',
                            phone_title=app.config["PHONE_TITLE"], 
                            phone_display_your_turn = app.config['PHONE_DISPLAY_YOUR_TURN'],
                            phone_center = app.config['PHONE_CENTER'],
                            phone_lines=phone_lines,
                            phone_your_turn_lines=phone_your_turn_lines,
                            phone_display_specific_message=app.config['PHONE_DISPLAY_SPECIFIC_MESSAGE'],
                            # CSS
                            phone_main_color=app.css_variable_manager.get_variable('phone', 'phone_main_color'),
                            phone_secondary_color=app.css_variable_manager.get_variable('phone', 'phone_secondary_color'),
                            phone_third_color=app.css_variable_manager.get_variable('phone', 'phone_third_color'),
                            phone_lines_font_size=app.css_variable_manager.get_variable('phone', 'phone_lines_font_size'),
                            phone_lines_font_weight=app.css_variable_manager.get_variable('phone', 'phone_lines_font_weight'),
                            # titre
                            phone_title_font_color=app.css_variable_manager.get_variable('phone', 'phone_title_font_color'),
                            phone_title_font_size=app.css_variable_manager.get_variable('phone', 'phone_title_font_size'),
                            phone_title_font_weight=app.css_variable_manager.get_variable('phone', 'phone_title_font_weight'),
                            phone_title_background_color=app.css_variable_manager.get_variable('phone', 'phone_title_background_color'),
                            phone_title_background_height=app.css_variable_manager.get_variable('phone', 'phone_title_background_height'),
                            phone_title_border_color=app.css_variable_manager.get_variable('phone', 'phone_title_border_color'),
                            phone_title_border_size=app.css_variable_manager.get_variable('phone', 'phone_title_border_size'),
                            # message spécifique
                            phone_specific_message_font_color=app.css_variable_manager.get_variable('phone', 'phone_specific_message_font_color'),
                            phone_specific_message_font_size=app.css_variable_manager.get_variable('phone', 'phone_specific_message_font_size'),
                            phone_specific_message_font_weight=app.css_variable_manager.get_variable('phone', 'phone_specific_message_font_weight'),
                            phone_specific_message_background_color=app.css_variable_manager.get_variable('phone', 'phone_specific_message_background_color'),
                            phone_specific_message_background_height=app.css_variable_manager.get_variable('phone', 'phone_specific_message_background_height'),
                            phone_specific_message_border_color=app.css_variable_manager.get_variable('phone', 'phone_specific_message_border_color'),
                            phone_specific_message_border_size=app.css_variable_manager.get_variable('phone', 'phone_specific_message_border_size'),
                            )

