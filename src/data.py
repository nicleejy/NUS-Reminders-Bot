import requests
import datetime

from datetime import date, timedelta
from errors import *
from utils import *


global academic_year
global sem_index

# AY:[start of sem 1, start of sem 2]
# Note: info for 2026 onwards has not yet been updated on the official NUS website
nus_academic_calendar = {'2021-2022': [date(2021, 8, 2), date(2022, 1, 10)],
                         '2022-2023': [date(2022, 8, 1), date(2023, 1, 9)],
                         '2023-2024': [date(2023, 8, 7), date(2024, 1, 15)],
                         '2024-2025': [date(2024, 8, 5), date(2025, 1, 13)]}


# Keeps track of windows where users can save a new timetable
relaxed_calendar = {'2021-2022': [date(2021, 5, 2), date(2021, 12, 5)],
                    '2022-2023': [date(2022, 5, 7), date(2022, 12, 4)],
                    '2023-2024': [date(2023, 5, 6), date(2023, 12, 10)],
                    '2024-2025': [date(2024, 5, 11), date(2024, 12, 8)],
                    '2025-2026': [date(2025, 5, 10), date(2025, 12, 8)]}


days_of_week = {'Monday': 0, 'Tuesday': 1, 'Wednesday': 2,
                'Thursday': 3, 'Friday': 4, 'Saturday': 5, 'Sunday': 6}


def configure_search():

    global academic_year
    global sem_index

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


configure_search()


def fetch_nusmods_data(ay):
    try:
        module_names = requests.get(
            "https://api.nusmods.com/v2/" + ay + "/moduleList.json")
    except Exception as e:
        print(f"Error occurred when querying API: {e}")
        raise YearNotFoundException(ay)

    database_of_module_names = module_names.json()
    return database_of_module_names


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
            print(f"Error occurred when querying API: {e}")
            raise

        if response.status_code == 404:
            print(
                f"Error occurred when querying API: {response.status_code}")
            raise YearNotFoundException(academic_year)

        dataBase = response.json()
        sem = 0
        sem_data = dataBase["semesterData"]
        user_timetable_sem = int(detectSem(link))
        if user_timetable_sem >= 3 or user_timetable_sem == 0:
            raise Exception("Semester does not exist.")
        else:
            for semester in sem_data:
                if semester["semester"] == user_timetable_sem:
                    semester_exists = True
                    break
                sem += 1
            if semester_exists:
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
                raise SemesterNotFoundException(user_timetable_sem)
    return modList


# Get module names from a given module code array


def get_module_name(arr):
    found_name = []
    try:
        mod_names = fetch_nusmods_data(academic_year)
        for mod in mod_names:
            for module_code in arr:
                if mod['moduleCode'] in module_code:
                    found_name.append(mod['title'] + ' (' + module_code + ')')
        return found_name
    except YearNotFoundException:
        raise


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
