import os.path
import requests
import telebot
import validators
import certifi
import uuid
import pytz
import datetime


from datetime import timedelta
from telebot.apihelper import ApiException
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.jobstores.mongodb import MongoDBJobStore
from apscheduler.executors.pool import ThreadPoolExecutor, ProcessPoolExecutor
from flask import Flask, request
from dateutil import parser
from pymongo import MongoClient
from errors import *
from utils import *
from data import *
from buttons import *
from messages import *


ca = certifi.where()
TOKEN = 'token'
bot = telebot.TeleBot(TOKEN)

server = Flask(__name__)

sg_timezone = pytz.timezone("Asia/Singapore")


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


# for debugging
main_user_timetable = {}

# maintains the process flow for each user
user_state = {}


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
            try:
                bot.send_message(
                    user, "🎉 Congratulations on completing the semester! Wishing you all the best for your final examinations! :)")
            except ApiException as e:
                print(f"Failed to send message to user {user}.")
                print(e)
            print("This is the final reminder for user " +
                  str(user) + ". There are no more reminders left.")
            collection.delete_one({"_id": user})
            print("Removing data of user " + str(user))

# Construct reminders for lessons


def make_reminder(job, userID, timing):
    state_handler(userID, "reminder", True)
    try:
        bot.send_message(userID, "📚 " + job[0] + " starts " + timing + " 📚", reply_markup=gen_markup_reminder(
            job[0], convertTime(job[2]) + ' - ' + convertTime(job[3]), job[4]))
        isCompleted(userID)
    except ApiException as e:
        print(e)


def refresh():

    academic_year = ""
    sem_index = 0

    if relaxed_calendar["2021-2022"][0] <= get_sg_time().date() < relaxed_calendar["2022-2023"][0]:
        academic_year = '2021-2022'
        if get_sg_time().date() >= relaxed_calendar["2021-2022"][1]:
            sem_index = 1
    elif relaxed_calendar["2022-2023"][0] <= get_sg_time().date() < relaxed_calendar["2023-2024"][0]:
        academic_year = '2022-2023'
        if get_sg_time().date() >= relaxed_calendar["2022-2023"][1]:
            sem_index = 1
    elif relaxed_calendar["2023-2024"][0] <= get_sg_time().date() < relaxed_calendar["2024-2025"][0]:
        academic_year = '2023-2024'
        if get_sg_time().date() >= relaxed_calendar["2023-2024"][1]:
            sem_index = 1
    elif relaxed_calendar["2024-2025"][0] <= get_sg_time().date() < relaxed_calendar["2025-2026"][0]:
        academic_year = '2024-2025'
        if get_sg_time().date() >= relaxed_calendar["2024-2025"][1]:
            sem_index = 1
    else:
        raise CalendarOutOfRangeError

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
refresh()

# Cron trigger to refresh date and time information daily
scheduler.add_job(refresh, trigger='cron', hour='4', minute='30',
                  jobstore="mongo", id="updateAY/Sem", replace_existing=True)


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

            mon_data, tues_data, weds_data, thurs_data, fri_data = (
                [] for i in range(5))

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

    if not isBusy(user):
        state_handler(user, "getModuleInfo", 1)
        bot.send_message(message.chat.id, "Send me a clear *photo* or *URL* of your NUSMods timetable:",
                         parse_mode='Markdown', reply_markup=cancel())
    else:
        bot.send_message(message.chat.id, "⚠️ Another job is in progress.")


@bot.message_handler(commands=['start', 'menu'])
def send_welcome(message):
    username = message.from_user.first_name
    bot.send_message(message.chat.id, "*Hello " + str(username) + "! " +
                     welcome_message, parse_mode='Markdown', reply_markup=gen_menu())


@bot.message_handler(commands=['help'])
def send_help(message):
    bot.send_message(message.chat.id, help_message, parse_mode='Markdown')


@bot.message_handler(content_types=["text"], func=lambda message: message.text == "❌ Cancel")
def terminate_operation(message):
    user = str(message.chat.id)
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

    if validators.url(message.text) and 'nusmods.com' in message.text:
        # handle errors in url, prevent generation of timetable with an invalid url
        try:
            state_handler(user, "isBusy", True)
            output = extractData(cleanTimetableLink(
                message.text), message.text)
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
                        message.chat.id, "⚠️ You have no remaining classes. If you wish to add a timetable for the next semester, please wait till after the examinations for the present semester :)")
                    state_handler(user, "isBusy", False)
                    terminate_operation(message)
                else:
                    userInfo = {"_id": message.chat.id, "userTimetable": output, "reminders": user_reminders, "reminderOn": False,
                                "list_of_jobs": None, "AY/Sem": [academic_year, sem_index], "module_names": module_names}
                    collection.insert_one(userInfo)
                    state_handler(user, "addTimetable", False)
                    state_handler(user, "isBusy", False)
                    bot.send_message(
                        message.chat.id, "✅ Your timetable has been successfully added!\n\nUse /activate to set your reminders.", reply_markup=gen_menu())
        except SemesterNotFoundException:
            bot.send_message(
                message.chat.id, "⚠️ I've detected some modules which are not ongoing during the specified semester. Please send your NUSMods timetable link again.\nPress ❌ Cancel to exit.")
            state_handler(user, "isBusy", False)
        except YearNotFoundException:
            bot.send_message(
                message.chat.id, "⚠️ Data for the next academic year is not yet available on NUSMods. Please try again in a few days :)")
            state_handler(user, "isBusy", False)
            terminate_operation(message)
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
    state_handler(user, "isBusy", True)
    make_uppercase = message.text.upper()
    try:
        result = parse_url(make_uppercase.split())
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
            if len(result) > 8:
                bot.send_message(
                    message.chat.id, "⚠️ Too many modules! Showing only the first 8 modules.")
            state_handler(user, "result", result)
            bot.send_message(
                message.chat.id, "📕 Click on a module for more information:", reply_markup=gen_markup(result))
            state_handler(user, "getModuleInfo", "option")
            state_handler(user, "isBusy", False)
    except YearNotFoundException:
        bot.send_message(
            message.chat.id, "⚠️ Data for the next academic year is not yet available on NUSMods. Please try again in a few days :)")
        state_handler(user, "isBusy", False)
        terminate_operation(message)


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
    result = user_state[str(user)]["result"]
    state_handler(user, "isBusy", True)
    if validators.url(message.text) and 'nusmods.com' in message.text:
        try:
            output = extractData(cleanTimetableLink(
                message.text), message.text)
            for i in output:
                result.append(i[0])
            state_handler(user, "semIndex", int(detectSem(message.text)))
            try:
                result = parse_url(result)
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
            except YearNotFoundException:
                bot.send_message(
                    message.chat.id, "⚠️ Data currently unavailable.")
                state_handler(user, "isBusy", False)
        except SemesterNotFoundException:
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
        if user_state[str(user)]["getModuleInfo"] == 1:
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
            except YearNotFoundException:
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


bot.polling(True)


@server.route('/' + TOKEN, methods=['POST'])
def getMessage():
    json_string = request.get_data().decode('utf-8')
    update = telebot.types.Update.de_json(json_string)
    bot.process_new_updates([update])
    return "!", 200


@server.route("/")
def webhook():
    bot.remove_webhook()
    bot.set_webhook(url='https:/herokuapp.com/' + TOKEN)
    return "!", 200


if __name__ == "__main__":
    server.run(host="0.0.0.0", port=int(os.environ.get('PORT', 5000)))
