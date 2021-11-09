from __future__ import print_function
from datetime import date, time, timedelta, timezone, datetime, timezone
from logging import exception
from requests.api import get
from telebot.types import Chat, ChatMember, InlineKeyboardMarkup, InlineKeyboardButton, ReplyKeyboardMarkup, Update, User
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.jobstores.mongodb import MongoDBJobStore
from apscheduler.executors.pool import ThreadPoolExecutor, ProcessPoolExecutor
from threading import Thread
from threading import Timer
from flask import Flask, request
from pytz import timezone
import os.path
import json
import requests
import telebot
import schedule
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


# response = requests.get("https://api.nusmods.com/v2/2018-2019/modules/" + module_code + ".json")

TOKEN = 'Token'
bot = telebot.TeleBot(TOKEN)

server = Flask(__name__)

sg_timezone = pytz.timezone("Asia/Singapore")

OCR_API_KEY = 'Key'

DATABASE_URL = os.environ.get("MONGODB_URI")

cluster = MongoClient(DATABASE_URL) # establish connection with database

db = cluster["NUSTimetable"]
collection = db["userInfo"]

bugdb = cluster["BugReports"]
bug_report = bugdb["Reports"]


schedules_database = db["apscheduler"]
jobstore_mongo = schedules_database["jobs"]

#jobstores for APScheduler
jobstores = {
    'mongo': MongoDBJobStore(client=cluster),
}
executors = {
    'default': ThreadPoolExecutor(20),
    'processpool': ProcessPoolExecutor(5)
}

global academic_year
global sem_index

scheduler = BackgroundScheduler(daemon=True, jobstores=jobstores, executors=executors, timezone="Asia/Taipei")

option_button = ['About', 'Eligible Modules', 'Exam Info', 'Details', 'Go back']
greetings = ['Good day!', 'Hello!', 'Hey there!', 'Hi there!', 'Greetings!', 'Hello there!', 'Hi!', 'Hey!']
goodbye = ['See you soon!', 'Have a nice day :)', 'Have a great day!', 'See you later!', 'Goodbye for now!', 'See you later!', 'Bye!']

#for debugging
main_user_timetable = {}

#maintains the process flow for each user
user_state = {}

#AY:[start of sem1, start of sem2]
#Note that 2025 info has yet to be updated
nus_academic_calendar = {'2021-2022' : [date(2021, 8, 2), date(2022, 1, 10)], '2022-2023' : [date(2022, 8, 1), date(2023, 1, 9)], '2023-2024' : [date(2023, 8, 7), date(2024, 1, 15)]}
days_of_week = {'Monday' : 0, 'Tuesday' : 1, 'Wednesday' : 2, 'Thursday' : 3, 'Friday' : 4, 'Saturday' : 5, 'Sunday' : 6}


def schedule_jobs(job_list, userID, timing):
    list_of_job = []
    for job in job_list:
        uniqueID = "user-" + str(userID) + "-" + str(uuid.uuid4())
        list_of_job.append(uniqueID)
        scheduler.add_job(make_reminder, 'date', run_date=job[1], args=[job, userID, timing], jobstore="mongo", replace_existing=True, id=uniqueID, misfire_grace_time=30)
    collection.update_one({"_id": userID}, {"$set":{"list_of_jobs": list_of_job}})    


#Construct reminders for lessons
def make_reminder(job, userID, timing):
    if userID not in user_state:
        user_state[str(userID)] = {"addTimetable": False, "getModuleInfo": 0, "result": [], "setTime": False, "modData":[], "isoModule":"", "reminder": True, "isBusy": False}  
    else:
        user_state[str(userID)]["reminder"] = True
    isCompleted(userID)
    bot.send_message(userID, "📚 Reminder: " + job[0] + " starts " + timing + " ✏️", reply_markup=gen_markup_reminder(job[0], convertTime(job[2]) + ' - ' + convertTime(job[3]), job[4]))


def get_sg_time():
    return datetime.datetime.now(sg_timezone)


def weekday_today():
    return datetime.datetime.today().weekday()

#function to update the reminder list 
def updateReminderList(list_of_reminders):
    print("Current date time in Singapore:")
    print(get_sg_time())
    updated_reminders = []
    for data in list_of_reminders:
        aware = sg_timezone.localize(data[1])
        if get_sg_time() <= aware:
            updated_reminders.append(data)
    return updated_reminders

#refresh the date and time information, clear timetables that do not match the current academic year
def configure_search():
    global academic_year
    global sem_index
    todaysDate = datetime.date.today()
    if date(2021, 8, 2) <= todaysDate < date(2022, 8, 1):
        ay = '2021-2022'
        if todaysDate >= date(2022, 1, 10):
            sem = 1
        else:
            sem = 0
    elif date(2022, 8, 1) <= todaysDate < date(2023, 8, 7):
        ay = '2022-2023'
        if todaysDate >= date(2023, 1, 9):
            sem = 1
        else:
            sem = 0
    elif date(2023, 8, 7) <= todaysDate < date(2025, 8, 1):
        ay = '2023-2024'
        if todaysDate >= date(2024, 1, 15):
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
            if result["AY/Sem"] != [ay, sem]:
                print("Timetable data does not match present semester/year. Removing data of user " + str(userID) + ".")
                collection.delete_one({"_id": userID})
                #need to remove jobs as well?
            current_reminders = (collection.find_one({"_id": userID}))["reminders"]
            collection.update_one({"_id": userID}, {"$set":{"reminders": updateReminderList(current_reminders)}})
            print("Updating reminders of user " + str(userID) + ".")
    else:
        print("No documents to update.")

    global user_state
    print("Clearing user state...")
    user_state = {}
    print("User state is now empty.")

    print("Refreshing date and time...")
    print("Currently: AY" + academic_year + " Semester " + str(sem_index + 1))
    scheduler.print_jobs()


configure_search()

#Cron trigger to refresh the date information daily to obtain the present academic year and semester 
scheduler.add_job(configure_search, trigger='cron', hour='4', minute='30', jobstore="mongo", id="updateAY/Sem", replace_existing=True)

def fetch_nusmods_data(ay):
    module_names = requests.get("https://api.nusmods.com/v2/" + ay + "/moduleList.json")
    database_of_module_names = module_names.json()
    return database_of_module_names


def randomNumber(arr):
    return random.randint(0, len(arr) - 1)

def detectSem(link):
    semList = link.split("timetable/", 1)[1]
    semester = semList[4:5]
    return semester

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
    
#processed = cleanTimetableLink(timetableLink)

#[module name, [classname, day, time, [weeks], venue], [classname, day, time]]
#{'ACC1701X': [['LEC', 'X1'], ['TUT', 'X07']], 'CFG1002': [['LEC', '09']], 'CS1101S': [['TUT', '09A'], ['REC', '02A'], ['LEC', '1']], 'CS1231S': [['TUT', '19'], ['LEC', '1']], 'MA1521': [['LEC', '1'], ['TUT', '3']], 'MA2001': [['LEC', '2'], ['TUT', '17']]}


def extractData(dict_of_mods, link):
    modList = []
    for key, value in dict_of_mods.items():
        subList = []
        subList.append(key)
        response = requests.get("https://api.nusmods.com/v2/" + academic_year + "/modules/" + key + ".json")
        dataBase = response.json()
        moduleData = dataBase["semesterData"][int(detectSem(link)) - 1]["timetable"]
        for session in moduleData:
            class_type = ((session["lessonType"]).upper())[0:3]
            for class_detail in value:
                if class_type == class_detail[0] and session["classNo"] == class_detail[1]:
                    required_data = [session["lessonType"] + ' ' + session["classNo"], session["day"], session["startTime"], session["endTime"], session["weeks"], session["venue"]]
                    subList.append(required_data)
        modList.append(subList)
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

#generates weekly overview
@bot.message_handler(commands=['classes'])
def gen_overview(message):
    generate_week = False
    user = message.chat.id
    if str(user) in user_state:
        if user_state[str(user)]["isBusy"] == True:
            bot.send_message(message.chat.id, "⚠️ Another job is in progress.")
        else:
            generate_week = True
    else:
        generate_week = True

    if generate_week == True:
        if collection.count_documents({"_id": user}) == 0:
                bot.send_message(message.chat.id, "⚠️ You have not added a timetable yet!\nEnter /add to save your timetable.")
        else:
            mon = datetime.date.today() - timedelta(days=weekday_today())
            tues = datetime.date.today() + timedelta(days =(1 - weekday_today()))
            weds = datetime.date.today() + timedelta(days =(2 - weekday_today()))
            thurs = datetime.date.today() + timedelta(days =(3 - weekday_today()))
            fri = datetime.date.today() + timedelta(days=(4 - weekday_today()))
            sat = datetime.date.today() + timedelta(days=(5 - weekday_today()))
            sun = datetime.date.today() + timedelta(days=(6 - weekday_today()))
            
            mon_data = []
            tues_data = []
            weds_data = []
            thurs_data = []
            fri_data = []
            sat_data = []
            sun_data = []

            current_reminders = (collection.find_one({"_id": user}))["reminders"]
            collection.update_one({"_id": user}, {"$set":{"reminders": updateReminderList(current_reminders)}})
            new_reminders = (collection.find_one({"_id": user}))["reminders"]
            print("Updating reminders of user " + str(user) + ".")

            for reminder in new_reminders:
                print(reminder)
                if reminder[1].date() == mon:
                    mon_data.append(reminder)
                elif reminder[1].date() == tues:
                    tues_data.append(reminder)
                elif reminder[1].date() == weds:
                    weds_data.append(reminder)
                elif reminder[1].date() == thurs:
                    thurs_data.append(reminder)
                elif reminder[1].date() == fri:
                    fri_data.append(reminder)
                elif reminder[1].date() == sat:
                    sat_data.append(reminder)
                elif reminder[1].date() == sun:
                    sun_data.append(reminder)

            def iterator(data):
                if len(data) == 0:
                    return "✅ You do not have anymore classes!\n"
                event = ""
                for i in data:
                    new_event = "◽ " + i[0] + " (" + convertTime(i[2]) + " to " + convertTime(i[3]) + ")\n"
                    event += new_event
                return event

            msg = "*AY" + academic_year + "   Semester " + str(sem_index + 1) + "\n\n🗓️ Your week at a glance* 🗓️\n\n*Monday (" + mon.strftime('%d/%m/%Y') + ")*\n\n" + iterator(mon_data) \
            + "\n*Tuesday (" + tues.strftime('%d/%m/%Y') + ")*\n\n" + iterator(tues_data)\
            + "\n*Wednesday (" + weds.strftime('%d/%m/%Y') + ")*\n\n" + iterator(weds_data)\
            + "\n*Thursday (" + thurs.strftime('%d/%m/%Y') + ")*\n\n" + iterator(thurs_data)\
            + "\n*Friday (" + fri.strftime('%d/%m/%Y') + ")*\n\n" + iterator(fri_data)\
            + "\n*Saturday (" + sat.strftime('%d/%m/%Y') + ")*\n\n" + iterator(sat_data)\
            + "\n*Sunday (" + sun.strftime('%d/%m/%Y') + ")*\n\n" + iterator(sun_data)
            bot.send_message(user, msg, parse_mode='Markdown')
            generate_week = False
    else:
        bot.send_message(message.chat.id, "⚠️ Another job is in progress.")
            



#compare the dates in the list of reminders to current day and returns only the future dates (datetime object is modified here to combine the time for scheduler)
def calibrate_reminder_start(list_of_reminders):
    today = datetime.date.today()
    calibrated_reminders = []
    for data in list_of_reminders:
        if today <= data[1]:
            time_object = datetime.time(int(data[2][:2]), int(data[2][2:]))
            date_object = data[1]
            combined_datetime = datetime.datetime.combine(date_object, time_object)
            calibrated_reminders.append([data[0], combined_datetime, data[2], data[3], data[4]])
    return calibrated_reminders

#turn off reminders, clear job list if all jobs have been executed
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
            print("This is the final reminder for user " + str(user) + ". There are no more reminders left.")
            collection.update_one({"_id": user}, {"$set":{"reminderOn": False}})
            collection.update_one({"_id": user}, {"$set":{"list_of_jobs": None}})
            print("Closing job list. Setting reminder state to False.")


def formatOutput(arr):
    initial = "Here are your classes for the week!"
    for mod in arr:
        initial += '\n' + mod[0] 
        for classes in mod[1:]:
            initial += "\n- " + classes[0] + " (" + classes[1] + " " + convertTime(classes[2]) + "-" + convertTime(classes[3]) + ")"
    return initial

def iterate_modules_for_image(arr):
    sifter = ['Total', 'Module']
    match_detected = []
    mod_names = fetch_nusmods_data(academic_year)
    if sifter[0] and sifter[1] in arr:
        for mod in mod_names:
            if mod['moduleCode'] in arr:
                match_detected.append(mod['title'].replace(',', '') + ' (' + mod['moduleCode'] + ')')
        return match_detected
    else:
        return 'error'

def iterate_modules_for_url(arr):
    match_detected = []
    mod_names = fetch_nusmods_data(academic_year)
    for mod in mod_names:
        if mod['moduleCode'] in arr:
            match_detected.append(mod['title'].replace(',', '') + ' (' + mod['moduleCode'] + ')')
    return match_detected
    
#If jobstore has no jobs, we need to set the reminder status to false
def ans_time_set(call):
        user = str(call.message.chat.id)
        if user in user_state:
            if user_state[user]["setTime"] == True:
                return True
            else:
                return False
        else:
            return False

def su_convert(bool):
    if bool:
        return "Yes"
    else:
        return "No"

def calc_workload(arr):
    try:
        count = 0
        for i in arr:
            count += int(i)
        return str(count) + ' hours'
    except:
        return 'Unable to retrieve data.'

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
                    initial = session[4][0] * 7
                add_days = 0
                gather_data = [moduleName + ' ' + session[0], nus_academic_calendar[this_AY][int(detectSem(link)) - 1] + timedelta(days = initial + days_of_week[session[1]]), session[2], session[3], session[5]]
                lesson_reminder.append(gather_data)
                weekList = session[4]

                for j in range(1, len(weekList)):
                    weeks_between = weekList[j] - weekList[j - 1]
                    skip = weeks_between * 7
                    add_days = add_days + skip
        
                    if weekList[j] > 6 and accounted_for_recess_week == False:
                        add_days = add_days + 7
                        accounted_for_recess_week = True

                    subsequent_weeks = [moduleName + ' ' + session[0], nus_academic_calendar[this_AY][int(detectSem(link)) - 1] + timedelta(days = initial + days_of_week[session[1]] + add_days), session[2], session[3], session[5]]  
                    lesson_reminder.append(subsequent_weeks)
            except Exception as e:
                print(e)
                continue

    reminder_list.extend(lesson_reminder)
    return reminder_list

def ammend_timings(adv_time, curr):
    print("Ammending timings to " + str(adv_time) + " minutes in advance.")
    new_reminders = []
    for reminder in curr:
        new_reminders.append([reminder[0], reminder[1] - timedelta(minutes=adv_time), reminder[2], reminder[3], reminder[4]])
    print("Updated reminders:")

    for i in new_reminders:
        print(i)
    return new_reminders


def isolate_module_code_from_callback(response):
    try:
        callback = response.split()[-1]
        module_code = callback[1:len(callback) - 1]
        return module_code
    except Exception as isolateModError:
        print(isolateModError)
        return False
        

def get_module_name(arr):
    found_name = []
    mod_names = fetch_nusmods_data(academic_year)
    for mod in mod_names:
        for module_code in arr:
            if mod['moduleCode'] in module_code:
                found_name.append(mod['title'] + ' (' + module_code + ')')
    return found_name


def gen_markup(detected_modules):
    markup = InlineKeyboardMarkup()
    markup.row_width = 1
    for module in detected_modules:
        markup.add(InlineKeyboardButton(str(module), callback_data=str(module)))
    return markup


def process_photo(msg):
    fileID = msg.photo[-1].file_id
    image_path = get_file(TOKEN, fileID)['file_path']
    image_url = 'https://api.telegram.org/file/bot' + TOKEN + '/' + image_path 
    ocr_response = requests.get('https://api.ocr.space/parse/imageurl?apikey=' + OCR_API_KEY + '&url=' + image_url + '&language=eng&detectOrientation=True&filetype=JPG&OCREngine=2&isTable=True&scale=True')
    imageInfo = ocr_response.json()
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


def gen_markup_info(input):
    markup = InlineKeyboardMarkup()
    markup.row_width = 2
    markup.add(InlineKeyboardButton(input[0], callback_data=input[0]), InlineKeyboardButton(input[1], callback_data=input[1]))
    markup.add(InlineKeyboardButton(input[2], callback_data=input[2]), InlineKeyboardButton(input[3], callback_data=input[3]))
    markup.add(InlineKeyboardButton(input[4], callback_data=input[4]))
    return markup


def gen_time_options():
    markup = InlineKeyboardMarkup()
    markup.row_width = 1
    markup.add(InlineKeyboardButton("10 minutes before", callback_data="10"))
    markup.add(InlineKeyboardButton("30 minutes before", callback_data="30"))
    markup.add(InlineKeyboardButton("1 hour before", callback_data="60"))
    markup.add(InlineKeyboardButton("2 hours before", callback_data="120"))
    markup.add(InlineKeyboardButton("3 hours before", callback_data="180"))
    markup.add(InlineKeyboardButton("1 day before", callback_data="1440"))
    markup.add(InlineKeyboardButton("Cancel", callback_data="x"))
    return markup

@bot.message_handler(commands=['info'])
def activate_info(message):
    user = str(message.chat.id)
    print("Current state:")
    print(user_state)
    print("Handled by info module.")
    if user not in user_state:
        user_state[user] = {"addTimetable": False, "getModuleInfo": 0, "result": [], "setTime": False, "modData":[], "isoModule":"", "reminder": False, "isBusy": False}  
    else:
        user_state[user]["modData"] = []
        user_state[user]["isoModule"] = ""
        user_state[user]["addTimetable"] = False
        user_state[user]["setTime"] = False
    
    if user_state[user]["isBusy"] == False:
        user_state[user]["getModuleInfo"] = 1
        bot.send_message(message.chat.id, "*Select one option:*\n\n1. Send me a clear photo of your NUS Mods timetable\n\n2. URL to your NUS Mods timetable", parse_mode='Markdown')
    else:
        bot.send_message(message.chat.id, "⚠️ Another job is in progress.")

@bot.message_handler(commands=['start', 'menu'])
def send_welcome(message):
    bot.send_message(message.chat.id, "*" + greetings[randomNumber(greetings)] +" Welcome to NUS Timetable Assistant* 📆\n\nHere's what I can do:\n\n⏰ Create *personalised* class reminders!\
    \n\n📷 Send in a photo/URL of your timetable for *module info*\n\n🔍 *Search* modules from NUS Mods\n\n📚 Get a *weekly overview* of your classes\
    \n\n\n1. /add to save a timetable.\
    \n\n2. /activate to turn reminders on.\
    \n\n3. /deactivate to turn reminders off.\
    \n\n4. /remove to remove saved timetable.\
    \n\n5. /classes to see weekly overview.\
    \n\n6. /search to enquire about any module.\
    \n\n7. /info to retrieve info from your timetable.\
    \n\n8. /cancel to end running tasks.\
    \n\n9. /bugs to report issues\
    \n\n10. /menu to bring up this menu!", parse_mode='Markdown')


@bot.message_handler(commands=['cancel'])
def terminate_operation(message):
    user = str(message.chat.id)

    if user in user_state:
        if user_state[user]["isBusy"] == False:
            user_state[user]["getModuleInfo"] = 0
            user_state[user]["result"] = []
            user_state[user]["modData"] = []
            user_state[user]["isoModule"] = ""
            user_state[user]["setTime"] = False
            user_state[user]["addTimetable"] = False
            bot.send_message(message.chat.id, goodbye[randomNumber(goodbye)])
        else:
            bot.send_message(message.chat.id, "⚠️ Another job is in progress.")       

    else:
        bot.send_message(message.chat.id, goodbye[randomNumber(goodbye)])


@bot.message_handler(commands=['search'])
def search(message):
    user = str(message.chat.id)
    if user not in user_state:
        user_state[user] = {"addTimetable": False, "getModuleInfo": 0, "result": [], "setTime": False, "modData":[], "isoModule":"", "reminder": False, "isBusy": False}  
    else:
        user_state[user]["modData"] = []
        user_state[user]["isoModule"] = ""
        user_state[user]["addTimetable"] = False
        user_state[user]["setTime"] = False

    if user_state[user]["isBusy"] == False:    
        user_state[user]["getModuleInfo"] = "search"
        bot.send_message(message.chat.id, "🔍 Please enter the module codes. Start each code on a new line:")
    else:
        bot.send_message(message.chat.id, "⚠️ Another job is in progress.")


@bot.message_handler(commands=['remove'])
def clearUserData(message):
    user = message.chat.id
    if str(user) not in user_state:
        user_state[str(user)] = {"addTimetable": False, "getModuleInfo": 0, "result": [], "setTime": False, "modData":[], "isoModule":"", "reminder": False, "isBusy": False}  
    else:
        user_state[str(user)]["getModuleInfo"] = 0
        user_state[str(user)]["modData"] = []
        user_state[str(user)]["isoModule"] = ""

    if user_state[str(user)]["isBusy"] == False:
        user_state[str(user)]["isBusy"] = True
        if collection.count_documents({"_id": user}) == 0:
            bot.send_message(message.chat.id, "⚠️ You have not added a timetable yet!\nEnter /add to save your timetable.")
            user_state[str(user)]["isBusy"] = False
        else:
            userData = collection.find_one({"_id": user})
            state = userData["reminderOn"]
            bot.send_message(user, "Processing... please wait!")
            if state == True:
                if userData["list_of_jobs"] != None:
                    for id in userData["list_of_jobs"]:
                        try:
                            scheduler.remove_job(id, jobstore="mongo")
                        except Exception as e:
                            print(e)
                            continue
            collection.delete_one({"_id": user})
            num_jobs = len(scheduler.get_jobs(jobstore="mongo"))
            user_state[str(user)]["isBusy"] = False
            print("Jobs and timetable have been cleared. There are " + str(num_jobs) + " remaining.")
            bot.send_message(message.chat.id, "✅ Your timetable has been removed successfully.")
    else:
        bot.send_message(message.chat.id, "⚠️ Job in progress, unable to remove timetable.")


@bot.message_handler(commands=['add'])
def processTimetable(message):
    user = str(message.chat.id)
    if user not in user_state:
        user_state[user] = {"addTimetable": True, "getModuleInfo": 0, "result": [], "setTime": False, "modData":[], "isoModule":"", "reminder": False, "isBusy": False}  
    else:
        user_state[user]["addTimetable"] = True
        user_state[user]["getModuleInfo"] = 0
        user_state[user]["modData"] = []
        
    if user_state[user]["isBusy"] == False:
        if collection.count_documents({"_id": int(user)}) != 0:
            bot.send_message(message.chat.id, "⚠️ You already have a saved timetable.\nEnter /remove to remove your timetable.")
            user_state[user]["addTimetable"] = False
        else:
            bot.send_message(message.chat.id, "📎 Please send your NUS Mods Timetable Link here!")
    else:
        user_state[user]["addTimetable"] = False
        bot.send_message(message.chat.id, "⚠️ Job in progress, unable to add timetable.")

    print("Current state:")
    print(user_state)

@bot.message_handler(commands=['deactivate'])
def stopReminders(message):
    user = message.chat.id 
    if str(user) not in user_state:
        user_state[str(user)] = {"addTimetable": False, "getModuleInfo": 0, "result": [], "setTime": False, "modData":[], "isoModule":"", "reminder": False, "isBusy": False}  
    else:
        user_state[str(user)]["getModuleInfo"] = 0

    if user_state[str(user)]["isBusy"] == False:
        user_state[str(user)]["isBusy"] = True
        if collection.count_documents({"_id": user}) == 0:
            bot.send_message(message.chat.id, "⚠️ You have not added a timetable yet!\nEnter /add to save your timetable.")
            user_state[str(user)]["isBusy"] = False
        else:
            userData = collection.find_one({"_id": user})
            state = userData["reminderOn"]
            if state == True and userData["list_of_jobs"] != None:
                bot.send_message(user, "Processing... please wait!")
                for id in userData["list_of_jobs"]:
                    try:
                        scheduler.remove_job(id, jobstore="mongo")
                    except Exception as e:
                        print(e)
                        continue
                collection.update_one({"_id": user}, {"$set":{"reminderOn": False}})
                collection.update_one({"_id": user}, {"$set":{"list_of_jobs": None}})
                bot.send_message(message.chat.id, "✅ All reminders have been deactivated!")
                num_jobs = len(scheduler.get_jobs(jobstore="mongo"))
                print("Jobs have been cleared. There are " + str(num_jobs) + " remaining.")
                scheduler.print_jobs()
                user_state[str(user)]["isBusy"] = False
            else:
                bot.send_message(message.chat.id, "⚠️ You have no active reminders.")
                user_state[str(user)]["isBusy"] = False
    else:
        bot.send_message(message.chat.id, "⚠️ Job in progress, unable to deactivate reminders.")
        
        

@bot.message_handler(commands=['activate'])
def activateReminders(message):
    user = message.chat.id
    if str(user) not in user_state:
        user_state[str(user)] = {"addTimetable": False, "getModuleInfo": 0, "result": [], "setTime": False, "modData":[], "isoModule":"", "reminder": False, "isBusy": False}  
    else:
        user_state[str(user)]["setTime"] = False
        user_state[str(user)]["getModuleInfo"] = 0

    if user_state[str(user)]["isBusy"] == False:
        if collection.count_documents({"_id": user}) == 0:
            bot.send_message(message.chat.id, "⚠️ You have not added a timetable yet!\nEnter /add to save your timetable.")
        else:
            result = collection.find_one({"_id": user})
            if result["reminderOn"] == True:
                bot.send_message(message.chat.id, "⚠️ You already have active reminders!")
            else:
                bot.send_message(message.chat.id, "Select a timing below:", reply_markup=gen_time_options())
                user_state[str(user)]["setTime"] = True
    else: 
        bot.send_message(message.chat.id, "⚠️ Job in progress, unable to activate reminders.")


@bot.message_handler(commands=['bugs'])
def reportBug(message):
    user = str(message.chat.id)
    bot.send_message(message.chat.id, "Please provide details about the problems encountered:")
    bot.send_message(message.chat.id, "Enter /cancel to exit.")
    if user not in user_state:
        user_state[user] = {"addTimetable": False, "getModuleInfo": "bug", "result": [], "setTime": False, "modData":[], "isoModule":"", "reminder": False, "isBusy": "False"}  
    else:
        user_state[user]["getModuleInfo"] = "bug"
        user_state[str(user)]["setTime"] = False
        user_state[str(user)]["addTimetable"] = False


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
    if "/" in message.text:
        user_state[user]["getModuleInfo"] = 0
        
    else:
        bugReport = {"_id": user + "BugReport-" + str(uuid.uuid4()) , "report": message.text}
        bug_report.insert_one(bugReport)
        user_state[user]["getModuleInfo"] = 0
        bot.send_message(message.chat.id, "Thank you for your feedback!")


reminder_timings = {"10": ["10 minutes", 10], "30": ["30 minutes", 30], "60": ["1 hour", 60], "120": ["2 hours", 120], "180": ["3 hours", 180], "1440": ["tomorrow!", "1 day", 1440]}

@bot.callback_query_handler(func=lambda call: ans_time_set(call))
def answer_set_time(call):
    user = call.message.chat.id
    user_state[str(user)]["setTime"] = False
    current_reminders = (collection.find_one({"_id": user}))["reminders"]
    collection.update_one({"_id": user}, {"$set":{"reminders": updateReminderList(current_reminders)}})
    print("Updating reminders.")

    for i in updateReminderList(current_reminders):
        print(i)

    result = collection.find_one({"_id": user})
    if call.data in reminder_timings:
        user_state[str(user)]["isBusy"] = True
        user_state[str(user)]["setTime"] = True
        bot.answer_callback_query(call.id, text=False, show_alert=False)
        attributes = reminder_timings[call.data]
        if call.data == "1440":
            timing = attributes[0]
            msg = "You will be alerted " + attributes[1] +  " in advance!"
            bot.send_message(user, "Processing... please wait!")
            schedule_jobs(ammend_timings(attributes[2], result["reminders"]), user, timing)
            user_state[str(user)]["setTime"] = False
            user_state[str(user)]["isBusy"] = False
        else:
            timing = "in " + attributes[0] + "!"
            msg = "You will be alerted " + attributes[0] +  " in advance!"
            bot.send_message(user, "Processing... please wait!")
            schedule_jobs(ammend_timings(attributes[1], result["reminders"]), user, timing)
            user_state[str(user)]["setTime"] = False
            user_state[str(user)]["isBusy"] = False
            #schedule_jobs(ammend_timings(attributes[1],  [['CS1231S Lecture 1', datetime.datetime(2021, 11, 5, 20, 34), '1500', '1600', 'E-Learn_C']]), user, timing)
        bot.send_message(user, "✅ Your reminders have been created successfully!") 
        collection.update_one({"_id": user}, {"$set":{"reminderOn": True}}) 
        scheduler.print_jobs() 
        bot.send_message(user, msg)

    elif call.data == "x":
        user_state[str(user)]["getModuleInfo"] = 0
        user_state[str(user)]["result"] = []
        user_state[str(user)]["addTimetable"] = False
        user_state[str(user)]["setTime"] = False
        user_state[str(user)]["modData"] = []
        user_state[str(user)]["isoModule"] = ""
        bot.answer_callback_query(call.id, text=False, show_alert=False)
        bot.send_message(user, goodbye[randomNumber(goodbye)])

    else:
        bot.answer_callback_query(call.id, text=False, show_alert=False)
        bot.send_message(call.message.chat.id, "⚠️ Button has expired!")


def saveTimetable(msg):
        user = str(msg.chat.id)
        if user in user_state:
            if user_state[user]["addTimetable"] == True:
                return True
        else:
            return False


@bot.message_handler(func=lambda message: saveTimetable(message))
def validate_and_save(message):
    userID = str(message.chat.id)
    global main_user_timetable
    print("Current state:")
    print(user_state)
    if validators.url(message.text) and 'nusmods.com' in message.text:
        #handle errors in url, prevent generation of timetable with an invalid url
        try:
            if (int(detectSem(message.text)) - 1) != sem_index:
                bot.send_message(message.chat.id, "⚠️ Please enter a timetable for the current semester. Enter /cancel to exit")
            else:
                user_state[userID]["isBusy"] = True
                output = extractData(cleanTimetableLink(message.text), message.text)
                #output represents the raw timetable data
                main_user_timetable['myTimetable'] = output
                unsorted_reminders = generate_reminders(output, message.text, academic_year)
                sorted_reminders = sorted(unsorted_reminders, key=lambda t: (t[1], t[2]))
                main_user_timetable['myReminders'] = calibrate_reminder_start(sorted_reminders)
                main_user_timetable['user_ID'] = message.chat.id
                user = message.chat.id
                if collection.count_documents({"_id": user}) == 0:
                #stores user ID, timetable and reminders to MongoDB (userTimetable, reminders)
                    userInfo = {"_id": message.chat.id, "userTimetable": output, "reminders": calibrate_reminder_start(sorted_reminders), "reminderOn":False, "list_of_jobs": None, "AY/Sem": [academic_year, sem_index]}
                    collection.insert_one(userInfo)
                    user_state[str(user)]["addTimetable"] = False
                    user_state[userID]["isBusy"] = False
                    print('Timetable information has been successfully added to the database.')
                    bot.send_message(message.chat.id, "✅ Your timetable has been successfully added!")
        except Exception as e:
            print(e)
            bot.send_message(message.chat.id, "⚠️ Not a valid URL. Please send your NUS Mods Timetable Link again. Enter /cancel to exit.")
    else:
        bot.send_message(message.chat.id, "⚠️ Not a valid URL. Please send your NUS Mods Timetable Link again. Enter /cancel to exit")


def searchFunction(msg):
        user = str(msg.chat.id)
        if user in user_state:
            if user_state[user]["getModuleInfo"] == "search":
                return True
        else:
            return False


@bot.message_handler(content_types=['text'], func=lambda message: searchFunction(message))
def search_module(message):
    user = str(message.chat.id)
    print('Handled by search function.')
    make_uppercase = message.text.upper()
    result = iterate_modules_for_url(make_uppercase.split())
    if len(result) == 0:
        bot.send_message(message.chat.id, "⚠️ No module found!")
    else:
        count1 = -1
        for module in result:
            count1 += 1
            if len(module.split(' ')) > 6:
                result[count1] = " ".join((module.split(' '))[0:4]) + '...' + (module.split(' '))[-1] 
        print(result)
        result.append('Cancel')
        user_state[user]["result"] = result
        bot.send_message(message.chat.id, "📕 Click on a module for more information:", reply_markup=gen_markup(result))
        user_state[user]["getModuleInfo"] = "option"


def checkActiveURL(msg):
        user = str(msg.chat.id)
        if user in user_state:
            if user_state[user]["getModuleInfo"] == 1:
                return True
        else:
            return False
        

@bot.message_handler(content_types=['text'], func=lambda message: checkActiveURL(message))
def handle_url_sent(message):
    user = str(message.chat.id)
    print('Handled by URL info function.')
    result = user_state[user]["result"]
    if validators.url(message.text) and 'nusmods.com' in message.text:
        try:
            output = extractData(cleanTimetableLink(message.text), message.text)
            for i in output:
                result.append(i[0])
            result = iterate_modules_for_url(result)
            count3 = -1
            for module in result:
                count3 += 1
                if len(module.split(' ')) > 6:
                    result[count3] = " ".join((module.split(' '))[0:4]) + '...' + (module.split(' '))[-1] 
            result.append('Cancel')
            user_state[user]["result"] = result
            bot.send_message(message.chat.id, "📚 Here are your modules for the semester! 📚\n\nSelect a module you'd like to know more about:", reply_markup=gen_markup(result))
            user_state[user]["getModuleInfo"] = 2
        except:
            bot.send_message(message.chat.id, "⚠️ Not a valid URL. Please send your NUS Mods Timetable Link again.")
    else:
        bot.send_message(message.chat.id, "⚠️ Not a valid URL. Please send your NUS Mods Timetable Link again.")


@bot.message_handler(content_types=['photo'])
def handle_image_sent(message):
    user = str(message.chat.id)
    if user in user_state:
        print("Current state:")
        print(user_state)
        if user_state[user]["getModuleInfo"] == 1:
            print('Handled by image info function.')
            bot.send_message(message.chat.id, 'Processing... please wait!')
            result = process_photo(message)
            if result == False:
                    bot.send_message(message.chat.id, '⚠️ A server error ocurred.\nPlease wait before sending me another photo.\nAlternatively, you may enter /cancel to exit.')
                    print("API might be down, check API status.")
            elif len(result) == 0:
                bot.send_message(message.chat.id, '⚠️ It seems you do not have any modules.')         
            elif 0 < len(result) < 8:
                if result == 'error':
                    bot.send_message(message.chat.id, '⚠️ Woops! Wrong image! Please send me a timetable from NUS Mods only.')
                    print('Wrong image file sent')
                else:
                    count1 = -1
                    for module in result:
                        count1 += 1
                        if len(module.split(' ')) > 6:
                            result[count1] = " ".join((module.split(' '))[0:4]) + '...' + (module.split(' '))[-1] 
                    bot.send_message(message.chat.id, 'Success!')
                    result.append('Cancel')
                    user_state[user]["result"] = result
                    bot.send_message(message.chat.id, "📚 Here are your modules for the semester! 📚\n\nSelect a module you'd like to know more about:", reply_markup=gen_markup(result))
                    user_state[user]["getModuleInfo"] = 2
            else:
                count1 = -1
                for module in result:
                    count1 += 1
                    if len(module.split(' ')) > 6:
                        result[count1] = " ".join((module.split(' '))[0:4]) + '...' + (module.split(' '))[-1] 
                bot.send_message(message.chat.id, '⚠️ Some modules may not have been identified correctly.')
                result.append('Cancel')
                user_state[user]["result"] = result
                bot.send_message(message.chat.id, "📚 Here are your modules for the semester! 📚\n\nSelect a module you'd like to know more about:", reply_markup=gen_markup(result))
                user_state[user]["getModuleInfo"] = 2


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
    user = str(call.message.chat.id)
    print('Handled by options initial callback query.')
    choice = call.data
    if choice == "Cancel":
        bot.answer_callback_query(call.id)
        bot.send_message(call.message.chat.id, goodbye[randomNumber(goodbye)])
        del user_state[user]
    else:
        print(isolate_module_code_from_callback(choice))
        if isolate_module_code_from_callback(choice) != False:
            isolate_module = isolate_module_code_from_callback(choice)
            user_state[user]["isoModule"] = isolate_module
            moduleInfo = requests.get("https://api.nusmods.com/v2/" + academic_year + "/modules/" + isolate_module + ".json")
            moduleInfoData = moduleInfo.json()
            user_state[user]["modData"] = moduleInfoData
            bot.answer_callback_query(call.id)
            bot.send_message(call.message.chat.id, "Select an option for " + isolate_module + ":", reply_markup=gen_markup_info(option_button))
            user_state[user]["getModuleInfo"] = 3
        else:
            bot.answer_callback_query(call.id)
            bot.send_message(call.message.chat.id, "⚠️ Button has expired!")


def mod_details(call):
        user = str(call.message.chat.id)
        if user in user_state:
            if user_state[user]["getModuleInfo"] == 3:
                return True
        else:
            return False


@bot.callback_query_handler(func=lambda call: mod_details(call))
def genModuleDetails(call):
    user = str(call.message.chat.id)
    moduleInfoData = user_state[user]["modData"]
    isolate_module = user_state[user]["isoModule"]
    result = user_state[user]["result"]
    print('Handled by options callback query.')
    if call.data == 'Go back':
        bot.answer_callback_query(call.id, text=False, show_alert=False)
        bot.send_message(call.message.chat.id, "Click on a module for more information:", reply_markup=gen_markup(result))
        user_state[user]["getModuleInfo"] = 2
    else:
        if call.data == 'About':
            bot.send_message(call.message.chat.id, 'ℹ️ *About - ' + get_module_name([isolate_module])[0] + '*\n\n' + moduleInfoData["description"], parse_mode='Markdown')
            bot.answer_callback_query(call.id, text=False, show_alert=None)
            bot.send_message(call.message.chat.id, "What else would you like to know about?", reply_markup=gen_markup_info(option_button))
        
        elif call.data == 'Details':
            try:
                su_option = moduleInfoData["attributes"]["su"]
                bot.send_message(call.message.chat.id, '📝 *Details - ' + get_module_name([isolate_module])[0] + '*\n\nFaculty: ' + moduleInfoData["faculty"] + '\nS/U Option: ' + su_convert(su_option) + '\nWeekly Workload: ' + calc_workload(moduleInfoData["workload"]), parse_mode='Markdown')
                bot.answer_callback_query(call.id, text=False, show_alert=False)
                bot.send_message(call.message.chat.id, "What else would you like to know?", reply_markup=gen_markup_info(option_button))
            except:
                bot.send_message(call.message.chat.id, '📝 *Details - ' + get_module_name([isolate_module])[0] + '*\n\nFaculty: ' + moduleInfoData["faculty"] + '\nWeekly Workload: ' + calc_workload(moduleInfoData["workload"]), parse_mode='Markdown')
                bot.answer_callback_query(call.id, text=False, show_alert=False)
                bot.send_message(call.message.chat.id, "What else would you like to know?", reply_markup=gen_markup_info(option_button))
        
        elif call.data == 'Eligible Modules':
            try:
                post_mod_eligibility = moduleInfoData["fulfillRequirements"]
                bot.answer_callback_query(call.id, text=False, show_alert=False)
                eligible_modules = '✏️ *Eligible Modules - ' + get_module_name([isolate_module])[0] + '*'
                if len(get_module_name(post_mod_eligibility)) == 0:
                    bot.send_message(call.message.chat.id, "This module is not linked to other eligible modules.")
                    bot.answer_callback_query(call.id, text=False, show_alert=False)
                else:
                    count = 0
                    for i in get_module_name(post_mod_eligibility):
                        count += 1
                        eligible_modules += '\n\n' + str(count) + '. ' + i
                    bot.send_message(call.message.chat.id, eligible_modules, parse_mode='Markdown')
                    eligible_modules = None
                    bot.send_message(call.message.chat.id, "What else would you like to know?", reply_markup=gen_markup_info(option_button))
            except:
                bot.send_message(call.message.chat.id, "This module is not linked to other eligible modules.")
                bot.answer_callback_query(call.id, text=False, show_alert=False)
                bot.send_message(call.message.chat.id, "What else would you like to know?", reply_markup=gen_markup_info(option_button))

        elif call.data == 'Exam Info':
            try:
                examDate = str(parser.parse(moduleInfoData["semesterData"][sem_index]["examDate"])).split(' ', 1)[0]
                examTime = str(moduleInfoData["semesterData"][sem_index]["examDate"])
                formatDate = examDate.split("-")
                newDate = formatDate[2] + "/" + formatDate[1] + "/" + formatDate[0]
                utc_time = datetime.datetime.fromisoformat(examTime[:-1])

                def utc_to_local(utc_dt):
                    return utc_dt.astimezone(timezone('Asia/Singapore'))

                newTime = utc_to_local(utc_time).time().strftime("%H:%M")
                stringTime = newTime.split(":")
                displayTime = convertTime(stringTime[0] + stringTime[1])
                examData = '✏️ *Exam Info - ' + get_module_name([isolate_module])[0] + '*\n\nDate: ' + newDate + '\nTime: ' + displayTime + '\nDuration: ' + str(moduleInfoData["semesterData"][sem_index]["examDuration"] / 60) + ' hours'
                bot.answer_callback_query(call.id, text=False, show_alert=False)
                bot.send_message(call.message.chat.id, examData, parse_mode='Markdown')
                bot.send_message(call.message.chat.id, "What else would you like to know?", reply_markup=gen_markup_info(option_button))
            except Exception as e:
                print(e)
                bot.send_message(call.message.chat.id, "There do not seem to be any examinations for this module.")
                bot.answer_callback_query(call.id, text=False, show_alert=False)
                bot.send_message(call.message.chat.id, "What else would you like to know?", reply_markup=gen_markup_info(option_button))
        else:
            bot.answer_callback_query(call.id)
            bot.send_message(call.message.chat.id, "⚠️ Button has expired!")



#[details, date, starttime, endtime, location]
# [['ACC1701X', ['Tutorial X07', 'Tuesday', '1300', '1400', [1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13], 'E-Learn_C'], ['Lecture X1', 'Thursday', '1000', '1200', [1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13], 'E-Learn_C']], 
# ['CFG1002', ['Lecture 09', 'Wednesday', '0600', '0800', [7, 8, 9, 10, 11, 12], 'E-Learn_B']], 
# ['CS1101S', ['Tutorial 09A', 'Tuesday', '1400', '1600', [2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13], 'COM1-0217'], ['Recitation 02A', 'Thursday', '0900', '1000', [2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13], 'E-Learn_C'], ['Lecture 1', 'Wednesday', '1000', '1200', [1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13], 'E-Learn_C'], ['Lecture 1', 'Friday', '1000', '1200', [1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13], 'E-Learn_C']], 
# ['CS1231S', ['Lecture 1', 'Thursday', '1200', '1400', [1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13], 'E-Learn_C'], ['Tutorial 19', 'Thursday', '1400', '1600', [1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13], 'COM1-0208'], ['Lecture 1', 'Friday', '1500', '1600', [1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13], 'E-Learn_C']], 
# ['MA1521', ['Lecture 1', 'Wednesday', '1800', '2000', [1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13], 'E-Learn_B'], ['Lecture 1', 'Friday', '1800', '2000', [1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13], 'E-Learn_B'], ['Tutorial 3', 'Wednesday', '0900', '1000', [3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13], 'E-Learn_B']], 
# ['MA2001', ['Lecture 2', 'Friday', '1200', '1400', [1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13], 'E-Learn_B'], ['Tutorial 17', 'Wednesday', '1500', '1600', [3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13], 'E-Learn_B']]]


#generates the display for reminders
def gen_markup_reminder(module, time, venue):
    callback_data = module.split(' ')[0]
    markup = InlineKeyboardMarkup()
    markup.row_width = 2
    markup.add(InlineKeyboardButton(str(module), callback_data='seen1'))
    markup.add(InlineKeyboardButton(str(time), callback_data='seen2'), InlineKeyboardButton(str(venue), callback_data='seen3'))
    return markup


def answer_reminders(call):
        user = str(call.message.chat.id)
        if user in user_state:
            if user_state[user]["reminder"] == True:
                return True
            else:
                return False
        else:
            return False


@bot.callback_query_handler(func=lambda call: answer_reminders(call))
def answerReminderCallback(call):
    user = str(call.message.chat.id)
    if call.data == 'seen1':
        bot.answer_callback_query(call.id, text=False, show_alert=False)
        user_state[user]["reminder"] = False
    elif call.data == 'seen2':
        bot.answer_callback_query(call.id, text=False, show_alert=False)
        user_state[user]["reminder"] = False
    elif call.data == 'seen3':
        bot.answer_callback_query(call.id, text=False, show_alert=False)
        user_state[user]["reminder"] = False


scheduler.start()
scheduler.print_jobs()

#For debugging reminders
#sample = "https://nusmods.com/timetable/sem-1/share?ACC1701X=LEC:X1,TUT:X14&CFG1002=LEC:09&CS1101S=TUT:09A,REC:02A,LEC:1&CS1231S=TUT:19,LEC:1&MA1521=LEC:1,TUT:3&MA2001=LEC:2,TUT:17https://nusmods.com/timetable/sem-1/share?ACC1701X=LEC:X1,TUT:X14&CFG1002=LEC:09&CS1101S=TUT:09A,REC:02A,LEC:1&CS1231S=TUT:19,LEC:1&MA1521=LEC:1,TUT:3&MA2001=LEC:2,TUT:17"

#output = extractData(cleanTimetableLink(sample), sample)
#print(output)
# unsorted_reminders = generate_reminders(output, sample, academic_year)
# sorted_reminders = sorted(unsorted_reminders, key=lambda t: (t[1], t[2]))
# for i in unsorted_reminders:
#     print(i)

@bot.callback_query_handler(func=lambda x: True)
def handle_unknown_callbacks(call):
    bot.answer_callback_query(call.id, text=False, show_alert=False)
    bot.send_message(call.message.chat.id, "⚠️ Button has expired!")


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
    bot.set_webhook(url='https://nus-timetable-bot.herokuapp.com/' + TOKEN)
    return "!", 200


if __name__ == "__main__":
    server.run(host="0.0.0.0", port=int(os.environ.get('PORT', 5000)))