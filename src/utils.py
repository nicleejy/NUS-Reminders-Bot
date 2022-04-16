import datetime
import pytz
import random
import re

from telebot.apihelper import get_file
from data import *
from pytz import timezone

sg_timezone = pytz.timezone("Asia/Singapore")
TOKEN = "token"
OCR_API_KEY = 'key'


def get_sg_time():
    return datetime.datetime.now(sg_timezone)


# function to update the reminder list

def updateReminderList(list_of_reminders):
    print("Current date/time in Singapore:")
    print(get_sg_time())
    updated_reminders = []
    for data in list_of_reminders:
        aware = sg_timezone.localize(data[1])
        if get_sg_time() <= aware:
            updated_reminders.append(data)
    return updated_reminders


# compare the dates in the list of reminders to current day and returns only the future dates (datetime object is modified here to combine the time for scheduler)
def calibrate_reminder_start(list_of_reminders):
    calibrated_reminders = []
    for data in list_of_reminders:
        if datetime.date.today() <= data[1]:
            date_object = data[1]
            combined_datetime = datetime.datetime.combine(
                date_object, datetime.time(int(data[2][:2]), int(data[2][2:])))
            calibrated_reminders.append(
                [data[0], combined_datetime, data[2], data[3], data[4]])
    return calibrated_reminders


def utc_to_local(utc_dt):
    return utc_dt.astimezone(timezone('Asia/Singapore'))


def randomNumber(arr):
    return random.randint(0, len(arr) - 1)


def detectSem(link):
    semList = link.split("timetable/", 1)[1]
    semester = semList[4:5]
    return semester

# Parse timetable URL and extract relevant information for search


def cleanTimetableLink(link):
    mod_details = {}
    modules = link.split('share?', 1)[1]
    split_module_det = modules.split('&')
    for mod in split_module_det:
        cleaned_data = []
        keys = mod.split('=')[0]
        values = mod.split('=')[1]
        subValues = values.split(',')
        for session in subValues:
            cleaned_data.append(session.split(':'))
        mod_details[keys] = cleaned_data
    return mod_details

# Example of the output obtained from the above function
# [module name, [classname, day, time, [weeks], venue], [classname, day, time]]
#{'ACC1701X': [['LEC', 'X1'], ['TUT', 'X07']], 'CFG1002': [['LEC', '09']], 'CS1101S': [['TUT', '09A'], ['REC', '02A'], ['LEC', '1']], 'CS1231S': [['TUT', '19'], ['LEC', '1']], 'MA1521': [['LEC', '1'], ['TUT', '3']], 'MA2001': [['LEC', '2'], ['TUT', '17']]}

# Extracted data in the following format
#[module_code, [lesson_type, day, starttime, endtime, venue]]
# [['ACC1701X', ['Tutorial X07', 'Tuesday', '1300', '1400', [1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13], 'E-Learn_C'], ['Lecture X1', 'Thursday', '1000', '1200', [1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13], 'E-Learn_C']],
#['CFG1002', ['Lecture 09', 'Wednesday', '0600', '0800', [7, 8, 9, 10, 11, 12], 'E-Learn_B']],
#['CS1101S', ['Tutorial 09A', 'Tuesday', '1400', '1600', [2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13], 'COM1-0217'], ['Recitation 02A', 'Thursday', '0900', '1000', [2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13], 'E-Learn_C'], ['Lecture 1', 'Wednesday', '1000', '1200', [1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13], 'E-Learn_C'], ['Lecture 1', 'Friday', '1000', '1200', [1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13], 'E-Learn_C']],
#['CS1231S', ['Lecture 1', 'Thursday', '1200', '1400', [1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13], 'E-Learn_C'], ['Tutorial 19', 'Thursday', '1400', '1600', [1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13], 'COM1-0208'], ['Lecture 1', 'Friday', '1500', '1600', [1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13], 'E-Learn_C']],
#['MA1521', ['Lecture 1', 'Wednesday', '1800', '2000', [1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13], 'E-Learn_B'], ['Lecture 1', 'Friday', '1800', '2000', [1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13], 'E-Learn_B'], ['Tutorial 3', 'Wednesday', '0900', '1000', [3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13], 'E-Learn_B']],
# ['MA2001', ['Lecture 2', 'Friday', '1200', '1400', [1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13], 'E-Learn_B'], ['Tutorial 17', 'Wednesday', '1500', '1600', [3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13], 'E-Learn_B']]]


def convertTime(input):
    afternoon = False
    h, m = int(input[0:2]), int(input[2:4])
    if h > 12:
        h = h - 12
        afternoon = True
    elif 9 < h < 11:
        afternoon = False
    elif h == 12:
        afternoon = True
    elif h == 0:
        h = 12
        afternoon = False

    def is_afternoon(h, m):
        if afternoon:
            return str(h) + ":" + "{:02d}".format(m) + "pm"
        else:
            return str(h) + ":" + "{:02d}".format(m) + "am"
    return is_afternoon(h, m)


# Deprecated function


def formatOutput(arr):
    initial = "Here are your classes for the week!"
    for mod in arr:
        initial += '\n' + mod[0]
        for classes in mod[1:]:
            initial += "\n- " + classes[0] + " (" + classes[1] + " " + convertTime(
                classes[2]) + "-" + convertTime(classes[3]) + ")"
    return initial


# Parse data returned from the OCR API and search NUSMods for module name


def parse_image(arr):
    filters = ['Total', 'Module']
    match_detected = []
    try:
        mod_names = fetch_nusmods_data(academic_year)
        if filters[0] and filters[1] in arr:
            for mod in mod_names:
                if mod['moduleCode'] in arr:
                    match_detected.append(mod['title'].replace(
                        ',', '') + ' (' + mod['moduleCode'] + ')')
            return match_detected
        else:
            return 'error'
    except YearNotFoundException:
        raise


# Parse data from URL string and search NUSMods for module name


def parse_url(arr):
    match_detected = []
    try:
        mod_names = fetch_nusmods_data(academic_year)
        for mod in mod_names:
            if mod['moduleCode'] in arr:
                match_detected.append(mod['title'].replace(
                    ',', '') + ' (' + mod['moduleCode'] + ')')
        return match_detected
    except YearNotFoundException:
        raise


# Checks if module is S/U-able


def su_convert(bool):
    if bool:
        return "Yes"
    return "No"

# Calculates the total workload (in number of hours) for a module


def calc_workload(arr):
    try:
        count = 0
        for i in arr:
            count += int(i)
        return str(count) + ' hours'
    except:
        return 'Unable to retrieve data.'


# Change the timings of reminders to user selection


def ammend_timings(adv_time, curr):
    new_reminders = []
    for reminder in curr:
        new_reminders.append([reminder[0], reminder[1] - timedelta(
            minutes=adv_time), reminder[2], reminder[3], reminder[4]])
    return new_reminders

# Returns only the module code from the inlineKeyboard string (used to search NUSMods)


def isolate_module_code_from_callback(response):
    try:
        callback = response.split()[-1]
        module_code = callback[1:len(callback) - 1]
        return module_code
    except Exception as isolateModError:
        print(isolateModError)
        return False


def process_photo(msg):
    fileID = msg.photo[-1].file_id
    image_path = get_file(TOKEN, fileID)['file_path']
    image_url = 'https://api.telegram.org/file/bot' + TOKEN + '/' + image_path
    ocr_response = requests.get('https://api.ocr.space/parse/imageurl?apikey=' + OCR_API_KEY + '&url=' +
                                image_url + '&language=eng&detectOrientation=True&filetype=JPG&OCREngine=2&isTable=True&scale=True')
    imageInfo = ocr_response.json()
    try:
        if imageInfo['IsErroredOnProcessing'] == False:
            text_from_photo = imageInfo['ParsedResults'][0]['ParsedText']
            processed = re.split('\t|\r|\n', text_from_photo)
            key_info = []
            for elem in processed:
                if elem != '' and len(elem) > 6:
                    key_info.extend(elem.split(' '))
            return parse_image(key_info)
        else:
            return False
    except TypeError as e:
        print(e)
        return False
