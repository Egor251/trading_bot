import sqlite3
import os

try:
    import configparser
except ImportError:
    import ConfigParser as configparser
import os

from support_tools import Tools


class DB:
    db_path = ''
    # conn = sqlite3.connect(db_path)  # или :memory: чтобы сохранить в RAM
    # cursor = conn.cursor()

    conn = None
    cursor = None

    path = "settings.ini"

    config = configparser.ConfigParser()
    config.read(path)

    def __init__(self):

        '''# Проблема была в том, что при обращении к этому классу из файла не в корневой папке создавалась новая БД. Решение ниже
        if os.path.exists('trading_db.db'):  # Проверяем, есть ли в нашей папке файл с БД
            self.db_path = 'trading_db.db'  # Если есть, то указываем путь
        elif os.path.exists('../trading_db.db'):  # Если не в нашей, значит уровнем выше
            self.db_path = '../trading_db.db'  # Если есть, то указываем путь'''

        self.db_path = Tools.look_for_db()

        self.conn = sqlite3.connect(self.db_path)
        self.cursor = self.conn.cursor()

        check = 0
        if os.stat(self.db_path).st_size:  # файл может создаваться с ошибками и быть просто пыстым файлом
            check = self.select('SELECT EXISTS(SELECT * FROM main_db)')  # Проверяем есть ли хоть что-то в таблице
        if not check:
            print('Creating DB file')
            self.create_db()
            self.refresh_db()

    def refresh_db(self):
        """
        Обновление БД
        """
        self.select(
            "select 'drop table ' || name || ';' from sqlite_master where type = 'table';")  # Конструкция роняет все таблицы даже не зная из названий
        for category in list(
                self.config):  # таблица заполняется из конфиг файла. БД нужна чтобы пользователь мог во время работы программы менять дефолтные значения из конфига на свои не переписывая конфиг
            if category != 'DEFAULT':
                for item in list([self.config[str(category)]]):
                    for i in list(item):
                        data = self.config[str(category)][str(i)]
                        self.insert(i, data, item.name)
        self.insert('abs_path', Tools.get_working_dir(), '')

    def replace(self, param, data):  # Заменяем текущее значение в БД на пользовательское
        sql = f'''SELECT * FROM main_db WHERE parameter = {"'" + param + "'"};'''
        current_state = self.select(sql)
        # print(current_state[0])
        category = current_state[0][0]
        if current_state[0][2] != data:
            sql = f'''DELETE FROM main_db WHERE parameter = {"'" + param + "'"};'''
            self.select(sql)
            self.insert(param, data, category)

    def create_db(self):

        # Создание таблицы
        self.cursor.execute("""CREATE TABLE IF NOT EXISTS main_db
                          (category text, parameter text, state text, UNIQUE(parameter, state))
                       """)

        self.cursor.execute("""CREATE TABLE IF NOT EXISTS optimiser
                                  (ticker text, type text, action text, price text, quantity text)
                               """)

        self.conn.commit()

    def select(self, sql):
        self.cursor.execute(sql)
        result = self.cursor.fetchall()
        return result

    def insert(self, param, data, category=''):

        data = [category, param, data]

        try:
            self.cursor.executemany("INSERT INTO main_db VALUES (?, ?,?)", (data,))
        except sqlite3.IntegrityError:
            pass
        self.conn.commit()

    def test_connection(self):  # Проверка соединения. Хотя применяется эта функция для инициализации init класса
        return self.select('SELECT EXISTS(SELECT * FROM main_db)')

    def get_abs_path(self):
        path = self.select("SELECT state FROM main_db WHERE parameter = 'abs_path';")
        return path[0][0]


if __name__ == '__main__':
    # DB().refresh_db()
    # test = list(DB().config[str(list(DB().config)[1])])
    # print(test)
    # print((DB().select('Select * from main_db')))
    DB().replace('driver', 'Quik')
    print(DB().get_abs_path())
    pass
