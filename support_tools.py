import datetime


class Time:

    @staticmethod
    def day_delta(start_day, last_day=str(datetime.date.today())):
        #start_day = '2021-01-01'
        def format_date(date):
            date = date.split('-')
            return datetime.date(int(date[0]), int(date[1]), int(date[2]))
        start_day = format_date(start_day)
        last_day = format_date(last_day)
        delta = last_day - start_day
        return int(str(delta).split(' ')[0])

