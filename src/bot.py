from __future__ import print_function
from datetime import date, time, timedelta, timezone, datetime, timezone
from telebot import types
from telebot.types import Chat, ChatMember, InlineKeyboardMarkup, InlineKeyboardButton, ReplyKeyboardMarkup, Update, User
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.jobstores.mongodb import MongoDBJobStore
from apscheduler.executors.pool import ThreadPoolExecutor, ProcessPoolExecutor
from flask import Flask, request
from pytz import timezone
import os.path
import requests
import telebot
from telebot.apihelper import get_file, get_file_url, send_message
import validators
import re
import random
import datetime
from dateutil import parser
from pymongo import MongoClient, message, results
import certifi
import uuid
import pytz

ca = certifi.where()
TOKEN = 'token'
bot = telebot.TeleBot(TOKEN)

server = Flask(__name__)

sg_timezone = pytz.timezone("Asia/Singapore")

OCR_API_KEY = 'ley'

DATABASE_URL = os.environ.get("MONGODB_URI")
cluster = MongoClient(DATABASE_URL)

db = cluster["NUSTimetable"]
collection = db["userInfo"]

bugdb = cluster["BugReports"]
bug_report = bugdb["Reports"]

schedules_database = db["apscheduler"]
jobstore_mongo = schedules_database["jobs"]

# jobstores for APScheduler
jobstores = {
    'mongo': MongoDBJobStore(client=cluster),
}
executors = {
    'default': ThreadPoolExecutor(20),
    'processpool': ProcessPoolExecutor(5)
}

global academic_year
global sem_index


class Error(Exception):
    pass


class SemesterNotFoundError(Error):
    # semester index not present in database
    pass


class YearNotFoundError(Error):
    # data for the year not present in database
    pass


scheduler = BackgroundScheduler(
    daemon=True, jobstores=jobstores, executors=executors, timezone="Asia/Taipei")


def state_handler(userID, field, status):
    id = str(userID)
    if id not in user_state:
        user_state[id] = {"addTimetable": False,
                          "getModuleInfo": 0,
                          "result": [], "setTime": False,
                          "modData": [], "isoModule": "",
                          "reminder": False, "isBusy": False,
                          "semIndex": 0}
    else:
        if field != "isBusy":
            user_state[id]["getModuleInfo"] = 0
            user_state[id]["setTime"] = False
            user_state[id]["addTimetable"] = False
            user_state[id]["semIndex"] = 0
    user_state[id][field] = status


def isBusy(userID):
    if str(userID) in user_state:
        if user_state[str(userID)]["isBusy"]:
            bot.send_message(userID, "⚠️ Another job is in progress.")
            return True
    return False


def gen_menu():
    markup = types.ReplyKeyboardMarkup(row_width=2, resize_keyboard=True)
    button1 = types.KeyboardButton("📚 My Classes")
    button2 = types.KeyboardButton("📆 Weekly Overview")
    button3 = types.KeyboardButton("📷 Info")
    button4 = types.KeyboardButton("🔍 Search")
    markup.add(button1, button2, button3, button4)
    return markup


# Notifications for users
message_to_users = ""


def notify(msg):
    results = collection.find({})
    user_list = []
    test = [738423299]
    if collection.count_documents({}) != 0:
        print(str(collection.count_documents({})) + " user records present.")
        for result in results:
            userID = result["_id"]
            user_list.append(userID)
    for id in user_list:
        bot.send_message(id, msg, reply_markup=gen_menu(),
                         parse_mode="Markdown")


option_button = ['About', 'Eligible Modules',
                 'Exam Info', 'Details', 'Go back']
goodbye = ['See you soon!', 'Have a nice day :)', 'Have a great day!',
           'See you later!', 'Goodbye for now!', 'See you later!', 'Goodbye!']

# for debugging
main_user_timetable = {}

# maintains the process flow for each user
user_state = {}

# AY:[start of sem1, start of sem2]
# Note that info for 2026 onwards has yet to be updated on the official NUS website
nus_academic_calendar = {'2021-2022': [date(2021, 8, 2), date(2022, 1, 10)],
                         '2022-2023': [date(2022, 8, 1), date(2023, 1, 9)],
                         '2023-2024': [date(2023, 8, 7), date(2024, 1, 15)],
                         '2024-2025': [date(2024, 8, 5), date(2025, 1, 13)]}

days_of_week = {'Monday': 0, 'Tuesday': 1, 'Wednesday': 2,
                'Thursday': 3, 'Friday': 4, 'Saturday': 5, 'Sunday': 6}


help_message = "*Help Menu*\n\n1. *How to add my timetable? 📚*\n\nUse the /add command and send in the link to your timetable from NUSMods.\
    \n\nUpon successfully saving your timetable, you will be able to use the rest of the features.\n\n\n2. *How to obtain weekly overview? 📆*\n\nUse the *📆 Weekly Overview* button to get a summary of your classes for the current week.\
    \n\nOnly upcoming classes are reflected in the weekly summary.\n\n\n3. *How do I set my reminders? ⏰*\n\nWith the /activate command, you can set how early in advance you want to be notified.\nIf you do not wish to receive reminders, use the /deactivate command.\
    \n\n\n4. *Where can I view my timetable? 🧾*\n\nAfter adding your timetable, you will be able to view class information such as venues and lesson times by using the *📚 My Classes* button.\
    \n\n\n5. *How to retrieve module information? ℹ️*\n\nYou may use the following commands to obtain information about specific modules.\n\n*🔍 Search*\
    \nUse this command and enter module codes on a new line.\
    \n\n*📷 Info*\nUse this to retrieve module information using either an image or a link to your NUSMods timetable.\
    \n\n\n6. *How to use image recognition feature? 📸*\n\nUse *📷 Info* and send in a PNG/JPG file of your timetable from NUSMods to retrieve data for all your modules.\n\nAlternatively, you may also send in a link to your timetable.\
    \n\n\n7. *How do I delete my timetable? 🗑*\n\nUse the /remove command to delete all saved timetable information. This feature also automatically deactivates any active reminders.\
    \n\n\n8. *Where do I report bugs? 🐞*\n\nShould you encounter any bugs while using the bot, please enter /bugs and report the issue in the next message.\
    \n\n\nThank you for using NUS Timetable Reminders bot. Hope it has helped you to attend your classes on time and make more informed decisions when choosing modules! Stay tuned for more features :)"


def schedule_jobs(job_list, userID, timing):
    list_of_job = []
    for job in job_list:
        uniqueID = "user-" + str(userID) + "-" + str(uuid.uuid4())
        list_of_job.append(uniqueID)
        scheduler.add_job(make_reminder, 'date', run_date=job[1], args=[
                          job, userID, timing], jobstore="mongo", replace_existing=True, id=uniqueID, misfire_grace_time=30)
    collection.update_one(
        {"_id": userID}, {"$set": {"list_of_jobs": list_of_job}})


# Turn off reminders, clear stored timetable if all jobs have been executed
def isCompleted(user):
    if collection.count_documents({"_id": user}) != 0:
        entry = collection.find_one({"_id": user})
        userSpecificJobs = []
        if entry["list_of_jobs"] != None:
            for jobID in entry["list_of_jobs"]:
                job = scheduler.get_job(jobID, jobstore="mongo")
                if job != None:
                    userSpecificJobs.append(job)
        if len(userSpecificJobs) == 0:
            bot.send_message(
                user, "🎉 Congratulations on completing the semester! Wishing you all the best for your final examinations! :)")
            print("This is the final reminder for user " +
                  str(user) + ". There are no more reminders left.")
            collection.delete_one({"_id": user})
            print("Removing data of user " + str(user))

# Construct reminders for lessons


def make_reminder(job, userID, timing):
    state_handler(userID, "reminder", True)
    isCompleted(userID)
    bot.send_message(userID, "📚 " + job[0] + " starts " + timing + " 📚", reply_markup=gen_markup_reminder(
        job[0], convertTime(job[2]) + ' - ' + convertTime(job[3]), job[4]))


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


def configure_search():

    global academic_year
    global sem_index

    if date(2021, 8, 2) <= get_sg_time().date() < date(2022, 8, 1):
        ay = '2021-2022'
        if get_sg_time().date() >= date(2021, 12, 5):
            sem = 1
        else:
            sem = 0
    elif date(2022, 8, 1) <= get_sg_time().date() < date(2023, 8, 7):
        ay = '2022-2023'
        if get_sg_time().date() >= date(2022, 12, 4):
            sem = 1
        else:
            sem = 0
    elif date(2023, 8, 7) <= get_sg_time().date() < date(2024, 8, 5):
        ay = '2023-2024'
        if get_sg_time().date() >= date(2023, 12, 10):
            sem = 1
        else:
            sem = 0
    elif date(2024, 8, 5) <= get_sg_time().date() < date(2025, 8, 3):
        ay = '2024-2025'
        if get_sg_time().date() >= date(2024, 12, 8):
            sem = 1
        else:
            sem = 0

    academic_year = ay
    sem_index = sem
    results = collection.find({})

    if collection.count_documents({}) != 0:
        print(str(collection.count_documents({})) + " user records present.")
        for result in results:
            userID = result["_id"]
            current_reminders = (collection.find_one({"_id": userID}))[
                "reminders"]
            collection.update_one({"_id": userID}, {
                                  "$set": {"reminders": updateReminderList(current_reminders)}})
            print("Updating reminders of user " + str(userID) + ".")
    else:
        print("No documents to update.")

    global user_state
    print("Clearing user state...")
    user_state = {}
    print("User state has been cleared.")

    print("Refreshing date and time...")
    print("Currently: AY" + academic_year + " Semester " + str(sem_index + 1))


# refresh date and time information and update user database
configure_search()

# Cron trigger to refresh date and time information daily
scheduler.add_job(configure_search, trigger='cron', hour='4', minute='30',
                  jobstore="mongo", id="updateAY/Sem", replace_existing=True)

# get data from NUSMods


def fetch_nusmods_data(ay):
    try:
        module_names = requests.get(
            "https://api.nusmods.com/v2/" + ay + "/moduleList.json")
    except Exception as e:
        print(e)
        return "data_not_found"
    database_of_module_names = module_names.json()
    return database_of_module_names


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


# Extract data for each lesson in the timetable
def extractData(dict_of_mods, link):
    modList = []
    for key, value in dict_of_mods.items():
        semester_exists = False
        subList = []
        subList.append(key)
        try:
            response = requests.get(
                "https://api.nusmods.com/v2/" + academic_year + "/modules/" + key + ".json")
        except Exception as e:
            print(e)
            return "data_not_found"
        dataBase = response.json()
        sem = 0
        sem_data = dataBase["semesterData"]
        user_timetable_sem = int(detectSem(link))
        if user_timetable_sem >= 3 or user_timetable_sem == 0:
            raise Exception("Specified semester does not exist.")
        else:
            for semester in sem_data:
                if semester["semester"] == user_timetable_sem:
                    semester_exists = True
                    break
                sem += 1
            if semester_exists != False:
                print("Found data for semester " +
                      str(user_timetable_sem) + ".")
                moduleData = sem_data[sem]["timetable"]
                for session in moduleData:
                    class_type = ((session["lessonType"]).upper())[0:3]
                    for class_detail in value:
                        if class_type == class_detail[0] and session["classNo"] == class_detail[1]:
                            required_data = [session["lessonType"] + ' ' + session["classNo"], session["day"], session["startTime"],
                                             session["endTime"], session["weeks"], session["venue"] if session["venue"] != "" else "No venue info"]
                            subList.append(required_data)
                modList.append(subList)
            else:
                print("No data found for semester  " +
                      str(user_timetable_sem) + ".")
                raise SemesterNotFoundError
    return modList


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

# generates weekly overview


@bot.message_handler(content_types=["text"], func=lambda message: message.text == "📆 Weekly Overview")
def gen_overview(message):
    user = message.chat.id

    if not isBusy(user):
        if collection.count_documents({"_id": user}) == 0:
            bot.send_message(
                message.chat.id, "⚠️ You haven't added a timetable yet.\nEnter /add to save your timetable.")
        else:
            current_date = datetime.datetime.now().astimezone(sg_timezone)
            current_weekday = current_date.weekday()

            if current_weekday == 5 or current_weekday == 6:
                offset = 7
            else:
                offset = 0

            mon = current_date - \
                timedelta(days=current_weekday) + timedelta(days=offset)
            tues = current_date + \
                timedelta(days=(1 - current_weekday) + offset)
            weds = current_date + \
                timedelta(days=(2 - current_weekday) + offset)
            thurs = current_date + \
                timedelta(days=(3 - current_weekday) + offset)
            fri = current_date + timedelta(days=(4 - current_weekday) + offset)

            mon_data = []
            tues_data = []
            weds_data = []
            thurs_data = []
            fri_data = []

            current_reminders = (collection.find_one({"_id": user}))[
                "reminders"]
            collection.update_one(
                {"_id": user}, {"$set": {"reminders": updateReminderList(current_reminders)}})
            new_reminders = (collection.find_one({"_id": user}))["reminders"]
            print("Updating reminders of user " + str(user) + ".")
            for reminder in new_reminders:
                if reminder[1].date() == mon.date():
                    mon_data.append(reminder)
                elif reminder[1].date() == tues.date():
                    tues_data.append(reminder)
                elif reminder[1].date() == weds.date():
                    weds_data.append(reminder)
                elif reminder[1].date() == thurs.date():
                    thurs_data.append(reminder)
                elif reminder[1].date() == fri.date():
                    fri_data.append(reminder)

            def iterator(data):
                if len(data) == 0:
                    return "🎉 No classes left 🎉\n"
                event = ""
                for i in data:
                    new_event = "◽ " + \
                        i[0] + " (" + convertTime(i[2]) + " to " + \
                        convertTime(i[3]) + ")\n"
                    event += new_event
                return event

            if offset == 7:
                title = "Next week at a glance"
            else:
                title = "This week at a glance"

            msg = "*🗓️ " + title + "* 🗓️\n\n*Monday " + mon.strftime('%d/%m/%Y') + "*\n\n" + iterator(mon_data) \
                + "\n*Tuesday " + tues.strftime('%d/%m/%Y') + "*\n\n" + iterator(tues_data)\
                + "\n*Wednesday " + weds.strftime('%d/%m/%Y') + "*\n\n" + iterator(weds_data)\
                + "\n*Thursday " + thurs.strftime('%d/%m/%Y') + "*\n\n" + iterator(thurs_data)\
                + "\n*Friday " + \
                fri.strftime('%d/%m/%Y') + "*\n\n" + iterator(fri_data)
            bot.send_message(user, msg, parse_mode='Markdown')


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


def iterate_modules_for_image(arr):
    sifter = ['Total', 'Module']
    match_detected = []
    mod_names = fetch_nusmods_data(academic_year)
    if mod_names == "data_not_found":
        print("Information not available for the current academic year.")
        raise YearNotFoundError
    else:
        if sifter[0] and sifter[1] in arr:
            for mod in mod_names:
                if mod['moduleCode'] in arr:
                    match_detected.append(mod['title'].replace(
                        ',', '') + ' (' + mod['moduleCode'] + ')')
            return match_detected
        else:
            return 'error'

# Parse data from URL string and search NUSMods for module name


def iterate_modules_for_url(arr):
    match_detected = []
    mod_names = fetch_nusmods_data(academic_year)
    if mod_names == "data_not_found":
        print("Information not available for the current academic year.")
        raise YearNotFoundError
    else:
        for mod in mod_names:
            if mod['moduleCode'] in arr:
                match_detected.append(mod['title'].replace(
                    ',', '') + ' (' + mod['moduleCode'] + ')')
        return match_detected

# Checks if module is S/U-able


def su_convert(bool):
    if bool:
        return "Yes"
    else:
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

# Create reminders based on the module information and current semester/academic year


def generate_reminders(arr, link, this_AY):
    reminder_list = []
    lesson_reminder = []

    for modSet in arr:
        moduleName = modSet[0]
        for session in modSet[1:]:
            accounted_for_recess_week = False
            try:
                if session[4][0] == 7:
                    initial = 7 + session[4][0] * 7
                else:
                    if sem_index == 0:
                        initial = session[4][0] * 7
                        print(f"Initial start for semester 1: {initial}")
                    elif sem_index == 1:
                        initial = (session[4][0] - 1) * 7
                        print(f"Initial start for semester 2: {initial}")
                add_days = 0
                gather_data = [moduleName + ' ' + session[0], nus_academic_calendar[this_AY][int(detectSem(
                    link)) - 1] + timedelta(days=initial + days_of_week[session[1]]), session[2], session[3], session[5]]
                lesson_reminder.append(gather_data)
                weekList = session[4]

                for j in range(1, len(weekList)):
                    weeks_between = weekList[j] - weekList[j - 1]
                    skip = weeks_between * 7
                    add_days = add_days + skip

                    if weekList[j] > 6 and not accounted_for_recess_week:
                        add_days = add_days + 7
                        accounted_for_recess_week = True

                    subsequent_weeks = [moduleName + ' ' + session[0], nus_academic_calendar[this_AY][int(detectSem(
                        link)) - 1] + timedelta(days=initial + days_of_week[session[1]] + add_days), session[2], session[3], session[5]]
                    lesson_reminder.append(subsequent_weeks)
            except Exception as e:
                # Modules which have no week array and contain a dictionary object instead
                print(e)
                continue
    reminder_list.extend(lesson_reminder)
    return reminder_list

# Change the timings of reminders to user selection


def ammend_timings(adv_time, curr):
    print("Ammending timings to " + str(adv_time) + " minutes in advance.")
    new_reminders = []
    for reminder in curr:
        new_reminders.append([reminder[0], reminder[1] - timedelta(
            minutes=adv_time), reminder[2], reminder[3], reminder[4]])
    print("Updated reminders:")

    for reminder in new_reminders:
        print(reminder)
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

# Get module names from a given module code array


def get_module_name(arr):
    found_name = []
    mod_names = fetch_nusmods_data(academic_year)
    if mod_names == "data_not_found":
        print("Information not available for the current academic year.")
        raise YearNotFoundError
    else:
        for mod in mod_names:
            for module_code in arr:
                if mod['moduleCode'] in module_code:
                    found_name.append(mod['title'] + ' (' + module_code + ')')
        return found_name


def process_photo(msg):
    fileID = msg.photo[-1].file_id
    image_path = get_file(TOKEN, fileID)['file_path']
    image_url = 'https://api.telegram.org/file/bot' + TOKEN + '/' + image_path
    ocr_response = requests.get('https://api.ocr.space/parse/imageurl?apikey=' + OCR_API_KEY + '&url=' +
                                image_url + '&language=eng&detectOrientation=True&filetype=JPG&OCREngine=2&isTable=True&scale=True')
    imageInfo = ocr_response.json()
    print(imageInfo)
    try:
        if imageInfo['IsErroredOnProcessing'] == False:
            text_from_photo = imageInfo['ParsedResults'][0]['ParsedText']
            processed = re.split('\t|\r|\n', text_from_photo)
            key_info = []
            for elem in processed:
                if elem != '' and len(elem) > 6:
                    key_info.extend(elem.split(' '))
            return iterate_modules_for_image(key_info)
        else:
            return False
    except TypeError as e:
        print(e)
        return False

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

# Generate user timetable


def gen_user_timetable(class_info, module_dict, userID):
    lesson_info = []
    count = 1
    for module in class_info:
        module_info = [module_dict[module[0]]]
        for lesson in module[1:]:
            start = convertTime(lesson[2])
            end = convertTime(lesson[3])
            duration = "(" + str(start) + " to " + str(end) + ")"
            day_time = lesson[1] + " " + duration
            lesson_type = lesson[0]
            venue = lesson[5]
            module_info.append([lesson_type, venue, day_time])
        lesson_info.append(module_info)
    result = "*AY" + academic_year + \
        "   Semester " + str(sem_index + 1) + "*\n\n"
    for mod in lesson_info:
        module_container = "*" + str(count) + ". " + mod[0] + "*"
        count += 1
        for classes in mod[1:]:
            format_lesson_info = "\n\n📚 *" + \
                classes[0] + "*\n- " + classes[1] + "\n- " + classes[2]
            module_container += format_lesson_info
        result += module_container.replace("_", " ") + "\n\n\n"
    bot.send_message(userID, result, parse_mode='Markdown')

# Data returned by lesson_info
# [['Programming Methodology II (CS2030S)', ['Thursday', '4:00pm to 6:00pm', 'Laboratory 16G', 'I3-0339'],
# ['Monday', '12:00pm to 2:00pm', 'Lecture 1', 'E-Learn_C'], ['Thursday', '11:00am to 12:00pm', 'Recitation 14', 'I3-AUD']],
# ['Data Structures and Algorithms (CS2040S)', ['Tuesday', '4:00pm to 6:00pm', 'Tutorial 14', 'E-Learn_C'],
# ['Monday', '4:00pm to 6:00pm', 'Lecture 1', 'E-Learn_C'], ['Wednesday', '2:00pm to 3:00pm', 'Lecture 1', 'E-Learn_C'],
# ['Thursday', '9:00am to 10:00am', 'Recitation 01', 'E-Learn_C']], ['Quantitative Reasoning with Data (GEA1000)',
# ['Tuesday', '12:00pm to 3:00pm', 'Tutorial E05', 'No venue info']], ['Asking Questions (GEQ1000)',
# ['Friday', '10:00am to 12:00pm', 'Tutorial D27', 'No venue info']]]


@bot.message_handler(content_types=["text"], func=lambda message: message.text == "📚 My Classes")
def get_user_timetable(message):
    user = message.chat.id

    if not isBusy(user):
        if collection.count_documents({"_id": user}) == 0:
            bot.send_message(
                message.chat.id, "⚠️ You haven't added a timetable yet.\nEnter /add to save your timetable.")
        else:
            userData = collection.find_one({"_id": user})
            timetable = userData["userTimetable"]
            module_dict = userData["module_names"]
            gen_user_timetable(timetable, module_dict, user)


@bot.message_handler(content_types=["text"], func=lambda message: message.text == "📷 Info")
def activate_info(message):
    user = message.chat.id
    print("Current state:")
    print(user_state)
    print("Handled by info module.")

    if not isBusy(user):
        state_handler(user, "getModuleInfo", 1)
        print(user_state)
        bot.send_message(message.chat.id, "Send me a clear *photo* or *URL* of your NUSMods timetable:",
                         parse_mode='Markdown', reply_markup=cancel())
    else:
        bot.send_message(message.chat.id, "⚠️ Another job is in progress.")


@bot.message_handler(commands=['start', 'menu'])
def send_welcome(message):
    bot.send_message(message.chat.id, "*Welcome to NUS Timetable Reminders!* 📆\
    \n\n\n*To create your reminders:*\
    \n\n1. */add* and send me your timetable URL 📎\
    \n\n2. */activate* and select a timing ⏰\
    \n\n3. Your reminders will be generated! ✅\
    \n\n\n*Here's what else I can do:*\
    \n\n📷  Send in a photo/URL of your timetable for *module info*\n\n🔍  *Search* modules from NUSMods\
    \n\n📚  Get a *weekly overview* of your timetable\
    \n\nClick on the *menu* button on the bottom left to explore more features!\
    \n\nIf you need any help, use the /help command :)", parse_mode='Markdown', reply_markup=gen_menu())


@bot.message_handler(commands=['help'])
def send_help(message):
    bot.send_message(message.chat.id, help_message, parse_mode='Markdown')


@bot.message_handler(content_types=["text"], func=lambda message: message.text == "❌ Cancel")
def terminate_operation(message):
    user = str(message.chat.id)
    print(user_state)

    if not isBusy(user):
        if user in user_state:
            user_state[user]["getModuleInfo"] = 0
            user_state[user]["result"] = []
            user_state[user]["modData"] = []
            user_state[user]["isoModule"] = ""
            user_state[user]["setTime"] = False
            user_state[user]["addTimetable"] = False
            user_state[user]["semIndex"] = 0

        bot.send_message(message.chat.id, goodbye[randomNumber(
            goodbye)], reply_markup=gen_menu())


@bot.message_handler(content_types=["text"], func=lambda message: message.text == "🔍 Search")
def search(message):
    user = message.chat.id
    if not isBusy(user):
        state_handler(user, "getModuleInfo", "search")
        bot.send_message(
            message.chat.id, "🔍 Enter the module codes below. Start each code on a new line:", reply_markup=cancel())


@bot.message_handler(commands=['remove'])
def clearUserData(message):
    user = message.chat.id
    if not isBusy(user):
        state_handler(user, "isBusy", True)
        if collection.count_documents({"_id": user}) == 0:
            bot.send_message(
                message.chat.id, "⚠️ You haven't added a timetable yet.\nEnter /add to save your timetable.")
            state_handler(user, "isBusy", False)
        else:
            userData = collection.find_one({"_id": user})
            state = userData["reminderOn"]
            bot.send_message(user, "Processing... please wait!")
            if state:
                if userData["list_of_jobs"] != None:
                    for id in userData["list_of_jobs"]:
                        try:
                            scheduler.remove_job(id, jobstore="mongo")
                        except Exception as e:
                            print(e)
                            continue
            collection.delete_one({"_id": user})
            num_jobs = len(scheduler.get_jobs(jobstore="mongo"))
            state_handler(user, "isBusy", False)
            print("Jobs and timetable have been cleared. There are " +
                  str(num_jobs) + " remaining.")
            bot.send_message(
                message.chat.id, "✅ Your timetable has been successfully removed.")
            bot.send_message(
                message.chat.id, "Want to add a new timetable? Just use /add :)")


@bot.message_handler(commands=['add'])
def processTimetable(message):
    user = message.chat.id

    if not isBusy(user):
        if collection.count_documents({"_id": int(user)}) != 0:
            bot.send_message(
                message.chat.id, "⚠️ My records show you have a saved timetable.\nEnter /remove to delete your timetable.")
        else:
            state_handler(user, "addTimetable", True)
            bot.send_message(
                message.chat.id, "📎 Please send your NUSMods Timetable Link here!", reply_markup=cancel())

    print("Current state:")
    print(user_state)


@bot.message_handler(commands=['deactivate'])
def stopReminders(message):
    user = message.chat.id
    state_handler(user, "addTimetable", True)

    if not isBusy(user):
        state_handler(user, "isBusy", True)
        if collection.count_documents({"_id": user}) == 0:
            bot.send_message(
                message.chat.id, "⚠️ You haven't added a timetable yet.\nEnter /add to save your timetable.")
            state_handler(user, "isBusy", False)
        else:
            userData = collection.find_one({"_id": user})
            state = userData["reminderOn"]
            if state and userData["list_of_jobs"] != None:
                bot.send_message(user, "Processing... please wait!")
                for id in userData["list_of_jobs"]:
                    try:
                        scheduler.remove_job(id, jobstore="mongo")
                    except Exception as e:
                        print(e)
                        continue
                collection.update_one(
                    {"_id": user}, {"$set": {"reminderOn": False}})
                collection.update_one(
                    {"_id": user}, {"$set": {"list_of_jobs": None}})
                bot.send_message(
                    message.chat.id, "✅ Your reminders have been deactivated!")
                num_jobs = len(scheduler.get_jobs(jobstore="mongo"))
                print("Jobs have been cleared. There are " +
                      str(num_jobs) + " remaining.")
                state_handler(user, "isBusy", False)
            else:
                bot.send_message(
                    message.chat.id, "⚠️ You have no active reminders.")
                state_handler(user, "isBusy", False)


@bot.message_handler(commands=['activate'])
def activateReminders(message):
    user = message.chat.id

    if not isBusy(user):
        if collection.count_documents({"_id": user}) == 0:
            bot.send_message(
                message.chat.id, "⚠️ You haven't added a timetable yet.\nEnter /add to save your timetable.")
        else:
            result = collection.find_one({"_id": user})
            if result["reminderOn"]:
                bot.send_message(
                    message.chat.id, "⚠️ You have active reminders.")
            elif len(result["reminders"]) != 0:
                bot.send_message(
                    message.chat.id, "Select a timing below:", reply_markup=gen_time_options())
                state_handler(user, "setTime", True)
            else:
                bot.send_message(
                    message.chat.id, "⚠️ It seems you do not have any upcoming classes.\nTo save a new timetable, enter /remove to delete your old timetable, then enter /add.")


@bot.message_handler(commands=['bugs'])
def reportBug(message):
    user = message.chat.id
    bot.send_message(
        message.chat.id, "Please provide details about the issues encountered:", reply_markup=cancel())
    state_handler(user, "getModuleInfo", "bug")

# Callback handler for bug reporting


def bug_reporting(msg):
    user = str(msg.chat.id)
    if user in user_state:
        if user_state[user]["getModuleInfo"] == "bug":
            return True
        else:
            return False
    else:
        return False


@bot.message_handler(content_types=['text'], func=lambda message: bug_reporting(message))
def save_bug_report(message):
    print("Current state:")
    print(user_state)
    user = str(message.chat.id)
    if "/" not in message.text:
        bugReport = {"_id": user + "BugReport-" +
                     str(uuid.uuid4()), "report": message.text}
        bug_report.insert_one(bugReport)
        bot.send_message(
            message.chat.id, "Thank you for your feedback!", reply_markup=gen_menu())
    state_handler(user, "getModuleInfo", 0)


reminder_timings = {"10": ["10 minutes", 10],
                    "30": ["30 minutes", 30],
                    "60": ["1 hour", 60],
                    "120": ["2 hours", 120],
                    "180": ["3 hours", 180],
                    "1440": ["tomorrow!", "1 day", 1440]}

# Callback handler for reminder setting


def ans_time_set(call):
    user = str(call.message.chat.id)
    if user in user_state:
        if user_state[user]["setTime"]:
            return True
        else:
            return False
    else:
        return False


@bot.callback_query_handler(func=lambda call: ans_time_set(call))
def answer_set_time(call):
    user = call.message.chat.id
    state_handler(user, "setTime", False)
    current_reminders = (collection.find_one({"_id": user}))["reminders"]
    collection.update_one(
        {"_id": user}, {"$set": {"reminders": updateReminderList(current_reminders)}})
    print("Updating reminders.")

    for i in updateReminderList(current_reminders):
        print(i)

    result = collection.find_one({"_id": user})
    if call.data in reminder_timings:
        state_handler(user, "isBusy", True)
        bot.answer_callback_query(call.id, text=False, show_alert=False)
        attributes = reminder_timings[call.data]
        if call.data == "1440":
            timing = attributes[0]
            msg = "I'll send you an alert " + attributes[1] + " in advance :)"
            bot.send_message(user, "Processing... please wait!")
            schedule_jobs(ammend_timings(
                attributes[2], result["reminders"]), user, timing)
            state_handler(user, "isBusy", False)
        else:
            timing = "in " + attributes[0] + "!"
            msg = "I'll send you an alert " + attributes[0] + " in advance :)"
            bot.send_message(user, "Processing... please wait!")
            schedule_jobs(ammend_timings(
                attributes[1], result["reminders"]), user, timing)
            state_handler(user, "isBusy", False)
            # schedule_jobs(ammend_timings(attributes[1],  [['CS1231S Lecture 1', datetime.datetime(2021, 11, 26, 13, 0), '1500', '1600', 'E-Learn_C'],
            #['Reminder Test', datetime.datetime(2021, 11, 25, 23, 43), '1500', '1600', 'E-Learn_C'],
            # ['CS1231S Lecture 1', datetime.datetime(2021, 11, 27, 20, 30), '1500', '1600', 'E-Learn_C']]), user, timing)
        bot.send_message(user, "✅ Your reminders have been created!")
        collection.update_one({"_id": user}, {"$set": {"reminderOn": True}})
        bot.send_message(user, msg)

    elif call.data == "x":
        user_state[str(user)]["getModuleInfo"] = 0
        user_state[str(user)]["result"] = []
        user_state[str(user)]["addTimetable"] = False
        user_state[str(user)]["setTime"] = False
        user_state[str(user)]["modData"] = []
        user_state[str(user)]["isoModule"] = ""
        bot.answer_callback_query(call.id, text=False, show_alert=False)
        bot.send_message(user, goodbye[randomNumber(
            goodbye)], reply_markup=gen_menu())

    else:
        bot.answer_callback_query(call.id, text=False, show_alert=False)
        bot.send_message(call.message.chat.id, "⚠️ Button has expired.")

# Callback handler for adding timetable


def saveTimetable(msg):
    user = str(msg.chat.id)
    if user in user_state:
        if user_state[user]["addTimetable"]:
            return True
    else:
        return False


@bot.message_handler(func=lambda message: saveTimetable(message))
def validate_and_save(message):
    user = str(message.chat.id)
    global main_user_timetable
    print("Current state:")
    print(user_state)

    if validators.url(message.text) and 'nusmods.com' in message.text:
        # handle errors in url, prevent generation of timetable with an invalid url
        try:
            state_handler(user, "isBusy", True)
            output = extractData(cleanTimetableLink(
                message.text), message.text)
            if output == 'data_not_found':
                bot.send_message(
                    message.chat.id, "⚠️ Data currently unavailable.")
                state_handler(user, "isBusy", False)
            else:
                # output represents the raw timetable data
                unsorted_reminders = generate_reminders(
                    output, message.text, academic_year)
                sorted_reminders = sorted(
                    unsorted_reminders, key=lambda t: (t[1], t[2]))
                module_names = {}
                module_codes = []
                for i in output:
                    module_codes.append(i[0])
                names = get_module_name(module_codes)
                for module in names:
                    code = str(isolate_module_code_from_callback(module))
                    module_names[code] = module

                user = message.chat.id
                if collection.count_documents({"_id": user}) == 0:
                    # stores user ID, timetable and reminders to MongoDB (userTimetable, reminders)
                    user_reminders = updateReminderList(
                        calibrate_reminder_start(sorted_reminders))
                    if len(user_reminders) == 0:
                        bot.send_message(
                            message.chat.id, "⚠️ You have no remaining classes, add a timetable for the next semester instead.\nPress ❌ Cancel to exit.")
                        state_handler(user, "isBusy", False)
                    else:
                        userInfo = {"_id": message.chat.id, "userTimetable": output, "reminders": user_reminders, "reminderOn": False,
                                    "list_of_jobs": None, "AY/Sem": [academic_year, sem_index], "module_names": module_names}
                        collection.insert_one(userInfo)
                        state_handler(user, "addTimetable", False)
                        state_handler(user, "isBusy", False)
                        username = message.from_user.first_name
                        print(
                            f'{username} has successfully added timetable to the database.')
                        bot.send_message(
                            message.chat.id, "✅ Your timetable has been successfully added!\n\nUse /activate to set your reminders.", reply_markup=gen_menu())
        except SemesterNotFoundError:
            bot.send_message(
                message.chat.id, "⚠️ I've detected some modules which are not ongoing during the specified semester. Please send your NUSMods timetable link again.\nPress ❌ Cancel to exit.")
            state_handler(user, "isBusy", False)
        except Exception as e:
            print(e)
            bot.send_message(
                message.chat.id, "⚠️ Not a valid URL. Please send your NUSMods timetable link again.\nPress ❌ Cancel to exit.")
            state_handler(user, "isBusy", False)
    else:
        bot.send_message(
            message.chat.id, "⚠️ Not a valid URL. Please send your NUSMods timetable link again.\nPress ❌ Cancel to exit.")
        state_handler(user, "isBusy", False)

# Callback handler for search function


def searchFunction(msg):
    user = str(msg.chat.id)
    if user in user_state:
        if user_state[user]["getModuleInfo"] == "search":
            return True
    else:
        return False


@bot.message_handler(content_types=['text'], func=lambda message: searchFunction(message))
def search_module(message):
    user = message.chat.id
    print('Handled by search function.')
    state_handler(user, "isBusy", True)
    make_uppercase = message.text.upper()
    try:
        result = iterate_modules_for_url(make_uppercase.split())
        if len(result) == 0:
            bot.send_message(message.chat.id, "⚠️ No module found.")
            state_handler(user, "isBusy", False)
        else:
            count1 = -1
            for module in result:
                count1 += 1
                if len(module.split(' ')) > 6:
                    result[count1] = " ".join(
                        (module.split(' '))[0:4]) + '... ' + (module.split(' '))[-1]
            print(result)
            if len(result) > 8:
                bot.send_message(
                    message.chat.id, "⚠️ Too many modules! Showing only the first 8 modules.")
            state_handler(user, "result", result)
            bot.send_message(
                message.chat.id, "📕 Click on a module for more information:", reply_markup=gen_markup(result))
            state_handler(user, "getModuleInfo", "option")
            state_handler(user, "isBusy", False)
    except YearNotFoundError:
        bot.send_message(message.chat.id, "⚠️ Data currently unavailable.")
        state_handler(user, "isBusy", False)


# Callback handler for URL info function
def checkActiveURL(msg):
    user = str(msg.chat.id)
    if user in user_state:
        if user_state[user]["getModuleInfo"] == 1:
            return True
    else:
        return False


@bot.message_handler(content_types=['text'], func=lambda message: checkActiveURL(message))
def handle_url_sent(message):
    user = message.chat.id
    print('Handled by URL info function.')
    result = user_state[str(user)]["result"]
    state_handler(user, "isBusy", True)
    if validators.url(message.text) and 'nusmods.com' in message.text:
        try:
            output = extractData(cleanTimetableLink(
                message.text), message.text)
            if output == 'data_not_found':
                bot.send_message(
                    message.chat.id, "⚠️ Data currently unavailable.")
                state_handler(user, "isBusy", False)
            else:
                for i in output:
                    result.append(i[0])
                state_handler(user, "semIndex", int(detectSem(message.text)))
                try:
                    result = iterate_modules_for_url(result)
                    count3 = -1
                    for module in result:
                        count3 += 1
                        if len(module.split(' ')) > 6:
                            result[count3] = " ".join(
                                (module.split(' '))[0:4]) + '... ' + (module.split(' '))[-1]
                    if len(result) > 8:
                        bot.send_message(
                            message.chat.id, "⚠️ Too many modules! Showing only the first 8 modules.")
                    state_handler(user, "result", result)
                    bot.send_message(
                        message.chat.id, "📚 Here are your modules for the semester! 📚\n\nSelect a module you'd like to know more about:", reply_markup=gen_markup(result))
                    state_handler(user, "getModuleInfo", 2)
                    state_handler(user, "isBusy", False)
                except YearNotFoundError:
                    bot.send_message(
                        message.chat.id, "⚠️ Data currently unavailable.")
                    state_handler(user, "isBusy", False)
        except SemesterNotFoundError:
            bot.send_message(
                message.chat.id, "⚠️ I've detected some modules which are not ongoing during the specified semester. Please send your NUSMods timetable link again.\nPress ❌ Cancel to exit.")
            state_handler(user, "isBusy", False)
        except Exception as e:
            print(e)
            bot.send_message(
                message.chat.id, "⚠️ Not a valid URL. Please send your NUSMods timetable link again.")
            state_handler(user, "isBusy", False)
    else:
        bot.send_message(
            message.chat.id, "⚠️ Not a valid URL. Please send your NUSMods timetable link again.")
        state_handler(user, "isBusy", False)

# Callback handler for image info function


@bot.message_handler(content_types=['photo'])
def handle_image_sent(message):
    user = message.chat.id
    if str(user) in user_state:
        print("Current state:")
        print(user_state)
        if user_state[str(user)]["getModuleInfo"] == 1:
            print('Handled by image info function.')
            state_handler(user, "isBusy", True)
            bot.send_message(message.chat.id, 'Processing... please wait!')
            try:
                result = process_photo(message)
                if not result:
                    bot.send_message(
                        message.chat.id, '⚠️ A server error ocurred.\nPlease wait before sending me another photo.\nAlternatively, you may press ❌ Cancel to exit.')
                    print("API might be down, check API status.")
                    state_handler(user, "isBusy", False)
                elif result == 'error':
                    bot.send_message(
                        message.chat.id, '⚠️ Woops! Please send me a timetable from NUSMods only.')
                    print('Wrong image file sent.')
                    state_handler(user, "isBusy", False)
                else:
                    if len(result) != 0:
                        if len(result) > 8:
                            bot.send_message(
                                message.chat.id, '⚠️ Some modules may not be identified correctly. Showing only the first 8 modules.')
                        count1 = -1
                        for module in result:
                            count1 += 1
                            if len(module.split(' ')) > 6:
                                result[count1] = " ".join(
                                    (module.split(' '))[0:4]) + '... ' + (module.split(' '))[-1]
                        state_handler(user, "result", result)
                        bot.send_message(
                            message.chat.id, "📚 Here are your modules for the semester! 📚\n\nSelect a module you'd like to know more about:", reply_markup=gen_markup(result))
                        state_handler(user, "getModuleInfo", 2)
                        state_handler(user, "isBusy", False)
                    else:
                        bot.send_message(
                            message.chat.id, '⚠️ It seems you do not have any modules.')
                        state_handler(user, "isBusy", False)
            except YearNotFoundError:
                bot.send_message(
                    message.chat.id, "⚠️ Data currently unavailable.")
                state_handler(user, "isBusy", False)


# Initial callback handler for module options
def ans_options(call):
    user = str(call.message.chat.id)
    if user in user_state:
        if (user_state[user]["getModuleInfo"] == 2 or user_state[user]["getModuleInfo"] == "option") and ("(" in call.data or "Cancel" in call.data):
            return True
        else:
            return False
    else:
        return False


@bot.callback_query_handler(func=lambda call: ans_options(call))
def callback_query(call):
    user = call.message.chat.id
    print('Handled by options initial callback query.')
    choice = call.data
    if choice == "Cancel":
        bot.answer_callback_query(call.id)
        bot.send_message(call.message.chat.id, goodbye[randomNumber(
            goodbye)], reply_markup=gen_menu())
        if str(user) in user_state:
            del user_state[str(user)]
    else:
        print(isolate_module_code_from_callback(choice))
        if isolate_module_code_from_callback(choice) != False:
            isolate_module = isolate_module_code_from_callback(choice)
            state_handler(user, "isoModule", isolate_module)
            try:
                moduleInfo = requests.get(
                    "https://api.nusmods.com/v2/" + academic_year + "/modules/" + isolate_module + ".json")
            except Exception as e:
                print(e)
                bot.send_message(user,
                                 "⚠️ Data currently unavailable.")
            moduleInfoData = moduleInfo.json()
            state_handler(user, "modData", moduleInfoData)
            bot.answer_callback_query(call.id)
            bot.send_message(user, "Select an option for " +
                             isolate_module + ":", reply_markup=gen_markup_info(option_button))
            state_handler(user, "getModuleInfo", 3)
        else:
            bot.answer_callback_query(call.id)
            bot.send_message(user, "⚠️ Button has expired.")

# Callback handler for module options


def mod_details(call):
    user = str(call.message.chat.id)
    if user in user_state:
        if user_state[user]["getModuleInfo"] == 3:
            return True
    else:
        return False


@bot.callback_query_handler(func=lambda call: mod_details(call))
def genModuleDetails(call):
    user = call.message.chat.id
    moduleInfoData = user_state[str(user)]["modData"]
    isolate_module = user_state[str(user)]["isoModule"]
    result = user_state[str(user)]["result"]
    print('Handled by options callback query.')
    if call.data == 'Go back':
        bot.answer_callback_query(call.id, text=False, show_alert=False)
        bot.send_message(
            call.message.chat.id, "Click on a module for more information:", reply_markup=gen_markup(result))
        state_handler(user, "getModuleInfo", 2)
    else:
        module_name = get_module_name([isolate_module])[0]
        sem_data = moduleInfoData["semesterData"]
        sem = 0
        semester_exists = False
        # get current semester
        set_semester = sem_index + 1
        print(user_state)
        # check if semester data is specified by the user
        if 0 < user_state[str(user)]["semIndex"] <= 2:
            print("Semester has been specified.")
            set_semester = user_state[str(user)]["semIndex"]
        else:
            print("Semester has not been specified, using data from semester " +
                  str(set_semester) + ".")
        # if semester is not specified, use current semester data
        for semester in sem_data:
            if semester["semester"] == set_semester:
                print("Timetable for semester " +
                      str(set_semester) + " found.")
                semester_exists = True
                break
            sem += 1

        if call.data == 'About':
            if moduleInfoData["description"] == '':
                bot.send_message(call.message.chat.id,
                                 "There is no description for this module.")
                bot.answer_callback_query(
                    call.id, text=False, show_alert=False)
            else:
                bot.send_message(call.message.chat.id, 'ℹ️ *About - ' + module_name +
                                 '*\n\n' + moduleInfoData["description"], parse_mode='Markdown')
                bot.answer_callback_query(call.id, text=False, show_alert=None)
            bot.send_message(call.message.chat.id, "What else would you like to know about?",
                             reply_markup=gen_markup_info(option_button))
        elif call.data == 'Details':
            try:
                su_option = moduleInfoData["attributes"]["su"]
                bot.send_message(call.message.chat.id, '📝 *Details - ' + module_name + '*\n\nFaculty: ' + moduleInfoData["faculty"] + '\nS/U Option: ' + su_convert(
                    su_option) + '\nWeekly Workload: ' + calc_workload(moduleInfoData["workload"]), parse_mode='Markdown')
                bot.answer_callback_query(
                    call.id, text=False, show_alert=False)
            except:
                try:
                    bot.send_message(call.message.chat.id, '📝 *Details - ' + module_name + '*\n\nFaculty: ' +
                                     moduleInfoData["faculty"] + '\nWeekly Workload: ' + calc_workload(moduleInfoData["workload"]), parse_mode='Markdown')
                    bot.answer_callback_query(
                        call.id, text=False, show_alert=False)
                except:
                    try:
                        bot.send_message(call.message.chat.id, '📝 *Details - ' + module_name +
                                         '*\n\nFaculty: ' + moduleInfoData["faculty"], parse_mode='Markdown')
                        bot.answer_callback_query(
                            call.id, text=False, show_alert=False)
                    except:
                        bot.send_message(
                            call.message.chat.id, 'Details are not available for this module.')
                        bot.answer_callback_query(
                            call.id, text=False, show_alert=False)
            bot.send_message(call.message.chat.id, "What else would you like to know about?",
                             reply_markup=gen_markup_info(option_button))
        elif call.data == 'Eligible Modules':
            try:
                post_mod_eligibility = moduleInfoData["fulfillRequirements"]
                bot.answer_callback_query(
                    call.id, text=False, show_alert=False)
                eligible_modules = '✏️ *Eligible Modules - ' + module_name + '*'
                eligible_names = get_module_name(post_mod_eligibility)
                if len(eligible_names) == 0:
                    bot.send_message(
                        call.message.chat.id, "This module is not linked to other eligible modules.")
                    bot.answer_callback_query(
                        call.id, text=False, show_alert=False)
                else:
                    count = 0
                    for i in eligible_names:
                        count += 1
                        eligible_modules += '\n\n' + str(count) + '. ' + i
                    bot.send_message(call.message.chat.id,
                                     eligible_modules, parse_mode='Markdown')
                    eligible_modules = None
            except:
                bot.send_message(
                    call.message.chat.id, "This module is not linked to other eligible modules.")
                bot.answer_callback_query(
                    call.id, text=False, show_alert=False)
            bot.send_message(call.message.chat.id, "What else would you like to know about?",
                             reply_markup=gen_markup_info(option_button))
        elif call.data == 'Exam Info':
            if not semester_exists:
                bot.send_message(
                    call.message.chat.id, "No details are available for the current semester, Semester " + str(sem_index + 1) + ".")
                bot.answer_callback_query(
                    call.id, text=False, show_alert=False)
            else:
                try:
                    examDate = str(parser.parse(
                        sem_data[sem]["examDate"])).split(' ', 1)[0]
                    examTime = str(sem_data[sem]["examDate"])
                    formatDate = examDate.split("-")
                    newDate = formatDate[2] + "/" + \
                        formatDate[1] + "/" + formatDate[0]
                    utc_time = datetime.datetime.fromisoformat(examTime[:-1])
                    newTime = utc_to_local(utc_time).time().strftime("%H:%M")
                    stringTime = newTime.split(":")
                    displayTime = convertTime(stringTime[0] + stringTime[1])
                    examData = '✏️ *Exam Info - ' + module_name + '*\n\nDate: ' + newDate + '\nTime: ' + \
                        displayTime + '\nDuration: ' + \
                        str(sem_data[sem]["examDuration"] / 60) + ' hours'
                    bot.answer_callback_query(
                        call.id, text=False, show_alert=False)
                    bot.send_message(call.message.chat.id,
                                     examData, parse_mode='Markdown')
                except Exception as e:
                    print(e)
                    bot.send_message(
                        call.message.chat.id, "There do not seem to be any examinations for this module.")
                    bot.answer_callback_query(
                        call.id, text=False, show_alert=False)
            bot.send_message(call.message.chat.id, "What else would you like to know about?",
                             reply_markup=gen_markup_info(option_button))
        else:
            bot.answer_callback_query(call.id)
            bot.send_message(call.message.chat.id, "⚠️ Button has expired.")

# Callback handler to catch button presses for reminders (prevents the Telegram API from attempting to reconnect if a callback query is not answered)


def answer_reminders(call):
    user = str(call.message.chat.id)
    if user in user_state:
        if user_state[user]["reminder"]:
            return True
        else:
            return False
    else:
        return False


@bot.callback_query_handler(func=lambda call: answer_reminders(call))
def answerReminderCallback(call):
    user = str(call.message.chat.id)
    if call.data == 'seen':
        bot.answer_callback_query(call.id, text=False, show_alert=False)
        state_handler(user, "reminder", False)


scheduler.start()
scheduler.print_jobs()

# For debugging reminders
#sample = "https://nusmods.com/timetable/sem-1/share?ACC1701X=LEC:X1,TUT:X14&CFG1002=LEC:09&CS1101S=TUT:09A,REC:02A,LEC:1&CS1231S=TUT:19,LEC:1&MA1521=LEC:1,TUT:3&MA2001=LEC:2,TUT:17https://nusmods.com/timetable/sem-1/share?ACC1701X=LEC:X1,TUT:X14&CFG1002=LEC:09&CS1101S=TUT:09A,REC:02A,LEC:1&CS1231S=TUT:19,LEC:1&MA1521=LEC:1,TUT:3&MA2001=LEC:2,TUT:17"

#output = extractData(cleanTimetableLink(sample), sample)
# print(output)
# unsorted_reminders = generate_reminders(output, sample, academic_year)
# sorted_reminders = sorted(unsorted_reminders, key=lambda t: (t[1], t[2]))
# for i in unsorted_reminders:
#     print(i)

# General callback handler to handle inactive button activations


@bot.callback_query_handler(func=lambda x: True)
def handle_unknown_callbacks(call):
    bot.answer_callback_query(call.id, text=False, show_alert=False)
    bot.send_message(call.message.chat.id, "⚠️ Button has expired.")


@server.route('/' + TOKEN, methods=['POST'])
def getMessage():
    json_string = request.get_data().decode('utf-8')
    update = telebot.types.Update.de_json(json_string)
    bot.process_new_updates([update])
    return "!", 200


if __name__ == "__main__":
    server.run(host="0.0.0.0", port=int(os.environ.get('PORT', 5000)))
