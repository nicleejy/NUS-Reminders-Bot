class SemesterNotFoundException(Exception):
    # Invalid semester or module does not take place during the specified semester
    def __init__(self, semester, *args):
        super().__init__(args)
        self.semester = semester

    def __str__(self):
        return f'No valid modules found for semester {self.semester}'


class YearNotFoundException(Exception):
    # Invalid year
    def __init__(self, year, *args):
        super().__init__(args)
        self.year = year

    def __str__(self):
        return f'No valid modules found for academic year {self.year}'


class CalendarOutOfRangeError(Exception):
    # data for the year not present in database
    def __init__(self, *args):
        super().__init__(args)

    def __str__(self):
        return 'Current date does not fall within any given date boundaries'
