from telebot.types import InlineKeyboardMarkup, InlineKeyboardButton
from telebot import types


def gen_menu():
    markup = types.ReplyKeyboardMarkup(row_width=2, resize_keyboard=True)
    button1 = types.KeyboardButton("📚 My Classes")
    button2 = types.KeyboardButton("📆 Weekly Overview")
    button3 = types.KeyboardButton("📷 Info")
    button4 = types.KeyboardButton("🔍 Search")
    markup.add(button1, button2, button3, button4)
    return markup


# Generate buttons for modules


def gen_markup(detected_modules):
    markup = InlineKeyboardMarkup()
    markup.row_width = 1
    shortened = detected_modules[0:8]
    shortened.append("Cancel")
    for module in shortened:
        markup.add(InlineKeyboardButton(
            str(module), callback_data=str(module)))
    return markup

# Generate buttons for module options


def gen_markup_info(input):
    markup = InlineKeyboardMarkup()
    markup.row_width = 2
    markup.add(InlineKeyboardButton(input[0], callback_data=input[0]), InlineKeyboardButton(
        input[1], callback_data=input[1]))
    markup.add(InlineKeyboardButton(input[2], callback_data=input[2]), InlineKeyboardButton(
        input[3], callback_data=input[3]))
    markup.add(InlineKeyboardButton(input[4], callback_data=input[4]))
    return markup

# Generate buttons for reminder time setting


def gen_time_options():
    markup = InlineKeyboardMarkup()
    markup.row_width = 2
    markup.add(InlineKeyboardButton("10 minutes before", callback_data="10"),
               InlineKeyboardButton("30 minutes before", callback_data="30"))
    markup.add(InlineKeyboardButton("1 hour before", callback_data="60"),
               InlineKeyboardButton("2 hours before", callback_data="120"))
    markup.add(InlineKeyboardButton("3 hours before", callback_data="180"),
               InlineKeyboardButton("1 day before", callback_data="1440"))
    markup.add(InlineKeyboardButton("Cancel", callback_data="x"))
    return markup

# Generate the display for reminders


def gen_markup_reminder(module, time, venue):
    markup = InlineKeyboardMarkup()
    markup.row_width = 2
    markup.add(InlineKeyboardButton(str(module), callback_data='seen'))
    markup.add(InlineKeyboardButton(str(time), callback_data='seen'),
               InlineKeyboardButton(str(venue), callback_data='seen'))
    return markup


def cancel():
    markup = types.ReplyKeyboardMarkup(row_width=1, resize_keyboard=True)
    button = types.KeyboardButton("❌ Cancel")
    markup.add(button)
    return markup
