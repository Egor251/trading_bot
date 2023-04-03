import sqlite3
try:
    import configparser
except ImportError:
    import ConfigParser as configparser
import os


class DB:
    conn = sqlite3.connect("trading_db.db")  # или :memory: чтобы сохранить в RAM
    cursor = conn.cursor()
    path = "settings.ini"

    config = configparser.ConfigParser()
    config.read(path)

    def __init__(self):
        check = 0
        if os.stat('trading_db.db').st_size:
            check = self.select('SELECT EXISTS(SELECT * FROM main_db)')
        if not check:
            print('Creating DB file')
            self.create_db()
            self.refresh_db()

    def refresh_db(self):

            self.select("select 'drop table ' || name || ';' from sqlite_master where type = 'table';")
            for category in list(self.config):
                if category != 'DEFAULT':
                    for item in list([self.config[str(category)]]):
                        for i in list(item):
                            data = self.config[str(category)][str(i)]
                            self.insert(i, data, item.name)

    def replace(self, param, data):
        sql = f'''SELECT * FROM main_db WHERE parameter = {"'" + param + "'"};'''
        current_state = self.select(sql)
        print(current_state[0])
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


if __name__ == '__main__':
    #DB().refresh_db()
    #test = list(DB().config[str(list(DB().config)[1])])
    #print(test)
    #print((DB().select('Select * from main_db')))
    DB().replace('driver', 'Quik')
