import numpy as np
import matplotlib.dates as mdates
import matplotlib.pyplot as plt
import pandas as pd
from matplotlib.dates import MONDAY, DateFormatter, DayLocator, WeekdayLocator
import talib as ta

import mplfinance
#from mplfinance import candlestick_ohlc
from mplfinance.original_flavor import candlestick_ohlc

import get_finance_data


class Metric:

    @staticmethod
    def display(dataframe, *addition):
        apd = mplfinance.make_addplot(addition[0])
        mplfinance.plot(
            dataframe,
            type='candle',
            style='charles',
            ylabel='Price ($)',
            volume=True,
            ylabel_lower='Shares\nTraded', addplot=apd
        )
        '''
        mplfinance.plot(
            dataframe,
            type='candle',
            style='charles',
            ylabel='Price ($)',
            volume=True,
            ylabel_lower='Shares\nTraded'
        )'''

    @staticmethod
    def calc_profit(run_mode, history, position=None, verbrose=1):
        # history struct: date | time | ticker | quantity | price | action (BUY or SELL)
        # TODO: calc_profit: run_mode
        if position is None:
            position = []
        history.loc[history['action'] == 'BUY', 'action'] = -1
        history.loc[history['action'] == 'SELL', 'action'] = 1
        result = 0
        current_quantity = 0
        tmp = history.iloc(0)[0]  # Первая строка из датасета
        if len(position) == 0:  # Если позицию не передали в качестве аргумента функции
            position = [tmp['date'], tmp['time'], tmp['ticker'], tmp['quantity'], tmp['price'], tmp['action']]  # Текущая открытая позиция
        basic_pos = 0

        for i, row in history.iterrows():  # Проход по всем сделкам построчно
            current_quantity += row['quantity'] * row['action'] * (-1)  # Текущее количество бумаг (сумматор)
            if np.sign(current_quantity) != position[-1]*-1 and np.sign(current_quantity) != 0:  # Проверка на переход из long в short и наоборот
                result += (position[4] - row['price']) * (position[3]) * row['action'] * np.sign(position[3]) * (-1)
                position = [row['date'], row['time'], row['ticker'], current_quantity, row['price'], row['action']]  # Перезаписываем текущую позицию, ведь она изменилась
            else:
                if position[-1] == row['action'] and basic_pos != 0:  # Если усредняемся, то считаем новую базовую цену позиции, basic_pos определяет была ли до этого открыта позиция
                    new_price = row['price'] * (row['quantity'] / (row['quantity'] + position[3])) + position[4] * (position[3] / (row['quantity'] + position[3]))  # Вычисляем новую базовую цену как сумму (цена * доля в новой позиции)
                    position = [row['date'], row['time'], row['ticker'], position[3] + current_quantity, new_price,
                                row['action']]
                else:  # Если не усредняемся, то считаем как обычно
                    result += round((position[4] - row['price'])*(position[3] - current_quantity)*row['action']*np.sign(position[3])*(-1), 2)

            if verbrose == 1:  # Выводить ли информацию в консоль, по умолчанию да
                print(f'Операция №{i}, текущая позиция: {current_quantity}, Текущая цена позиции {position[4]} текущая прибыль: {result}')
            position[3] = current_quantity  # Так как после операции количество бумаг изменилось перезаписываем в position
            basic_pos = 1
        return result

    @staticmethod
    def macd(data):
        data["macd"], data["macd_signal"], data["macd_hist"] = ta.MACD(data['close'])
        print(f'macd: {data["macd"]}')
        print(f'macd_hist: {data["macd_hist"]}')
        print(f'macd_signal: {data["macd_signal"]}')
        return data["macd"]


if __name__ == "__main__":
    test_trading_data = pd.DataFrame({'date': ['2023-01-12', '2023-01-12', '2023-01-12', '2023-01-12', '2023-01-12'],
                                      'time': ['10:05', '10:06', '10:30', '12:00', '13:02'],
                                      'ticker': ['SBER', 'SBER', 'SBER', 'SBER', 'SBER'],
                                      'quantity': [100, 100, 50, 100, 50],
                                      'price': [120.0, 100, 135.2, 150, 125],
                                      'action': ['BUY', 'BUY', 'SELL', 'SELL', 'BUY']
                                      })
    print(test_trading_data)
    Metric().calc_profit('test', test_trading_data)

    #test = get_finance_data.Info().alphavantage('IBM', "5min")
    #average = Metric().moving_average(a, 3)
    #average = Metric.macd(test)
    #Metric.display(test, average)

