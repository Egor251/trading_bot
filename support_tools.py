import datetime
import os
import importlib.util
from prettytable import PrettyTable


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


class Tools:

    @staticmethod
    def get_working_dir():
        file_path = os.path.basename(__file__)
        abs_path = os.path.abspath(__file__).replace(file_path, '')
        return abs_path

    @staticmethod
    def look_for_db():
        # Проблема была в том, что при обращении к этому классу из файла не в корневой папке создавалась новая БД. Решение ниже
        if os.path.exists('trading_db.db'):  # Проверяем, есть ли в нашей папке файл с БД
            db_path = 'trading_db.db'  # Если есть, то указываем путь
        elif os.path.exists('../trading_db.db'):  # Если не в нашей, значит уровнем выше
            db_path = '../trading_db.db'  # Если есть, то указываем путь
        else:
            db_path = 'trading_db.db'
        return db_path

    @staticmethod
    def cut_py(data):  # Отрезает .py от файла
        output = []
        for item in data:
            output.append(item[:-3])
        return output

    @staticmethod
    def make_dict(keys, values):
        output = {}
        for index, item in enumerate(keys):
            output[item] = values[index]
        return output

    @staticmethod
    def parse_dir(dir):  # Находит все файлы в папке
        data = os.listdir(dir)
        try:
            data.remove('__pycache__')
        except ValueError:
            pass
        return data

    @staticmethod
    def parse_file(file, attribute):  # Находит интересующую переменную в файле
        tmp = ''
        with open(file) as func:
            for line in func:
                if line.find(attribute) != -1:  # attribute это имя переменной, которую ищем в файле
                    tmp = line.split('=')[-1]  # Да, это должна быть переменная, парсим по строкам и выделяем значение по наличию =
                    break
            return tmp

    @staticmethod
    def make_table(column_name, data):
        tab = PrettyTable(column_name)  # Шапка таблички
        tab.add_rows(data)
        tab.align = 'l'
        tab.align[column_name[0]] = 'c'
        tab.padding_width = 5

        print(tab)


class Functionality:

    def run_module(self, path, func, attr=''):
        '''module_name = path.split('/')[1][:-3]
        module_spec = importlib.util.spec_from_file_location(
            module_name, path)
        module = module_spec.loader.load_module()'''
        module = self.import_module(path)
        #action = module.Strategy().run(attr)
        command = f'module.Strategy().{func}({attr})'
        action = eval(command)
        #print(f'Действие: {action}')  # Раскоментировать для дебага
        return action

    def import_module(self, path):
        module_name = path.split('/')[-1][:-3]
        module_spec = importlib.util.spec_from_file_location(
            module_name, path)
        module = module_spec.loader.load_module()
        return module

if __name__ == '__main__':
    Functionality().run_module('Strategies/test_strategy.py', 'get_attr', '"optimization_parameters"')
    pass
