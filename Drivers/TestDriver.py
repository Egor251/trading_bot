import pandas as pd

from structy import Driver
import asyncio
import time
import random


class TestDriver(Driver):

    @staticmethod
    def date_from_data(data):
        date = f"{data['day']}.{data['month']}.{data['year']}"
        time = f"{data['hour']}:{data['min']}"
        return date, time

    def get_portfolio(self):

        positions = {'Type': [], 'Class': [], 'Ticker': [], 'Quantity': [], 'Entry_price': [], 'Current_price': []}
        order = {'Type': [], 'Class': [], 'Number': [], 'Ticker': [], 'Quantity': [], 'Price': []}

        positions['Type'].append('stock')
        positions['Class'].append('FQBR')
        positions['Ticker'].append('GOOGL')
        positions['Quantity'].append('1')
        positions['Entry_price'].append('100')
        positions['Current_price'].append('120')

        order['Type'].append('Limit')
        order['Class'].append('FQBR')
        order['Number'].append('1')
        order['Ticker'].append('TSLA')
        order['Quantity'].append('1')
        order['Price'].append("150")

        pos = pd.DataFrame(positions)
        orderss = pd.DataFrame(order)
        return pos, orderss

    def get_DOM(self, class_code, sec_code):
        class_code = 'FQBR'
        sec_code = 'TSLA'
        #dom = self.qpProvider.GetQuoteLevel2(class_code, sec_code)['data']  # Getting DOM
        dom = {'a': 1}
        dom = pd.Series(dom)
        return dom

    def get_candles(self, class_code, ticker, interval=1, quantity=1):
        output_dict = {'date': [], 'time': [], 'open': [], 'close': [], 'low': [],
                       'high': [], 'volume': []}

        new_bars = [{'datetime': {'year': 2000, 'day': 1, 'month': 1, 'hour': 1, 'min': 1, 'sec': 1},
                     'open': 100,
                     'close': 120,
                     'low': 90,
                     'high': 130,
                     'volume': 25}]  # Getting all candles from your driver
        # print(new_bars)

        for item in new_bars:
            date, time = self.date_from_data(item['datetime'])
            output_dict['date'].append(date)
            output_dict['time'].append(time)
            output_dict['open'].append(item['open'])
            output_dict['close'].append(item['close'])
            output_dict['low'].append(item['low'])
            output_dict['high'].append(item['high'])
            output_dict['volume'].append(item['volume'])
        candles = pd.DataFrame(output_dict)
        # print(candles)
        return candles

    def candles_stream(self, class_code, ticker, interval=1):
        while True:
            stream = self.get_candles(class_code, ticker, interval)
            time.sleep(interval*60)
            return stream

    def test(self):
        t = time.localtime()
        current_time = time.strftime("%H:%M:%S", t)
        print(current_time)
        return 1
