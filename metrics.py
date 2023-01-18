import numpy
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

    def moving_average(self, data, n):

        data = pd.Series(data).rolling(window=n).mean().iloc[n - 1:].values
        print(data)
        return data

    @staticmethod
    def macd(data):
        data["macd"], data["macd_signal"], data["macd_hist"] = ta.MACD(data['close'])
        print(f'macd: {data["macd"]}')
        print(f'macd_hist: {data["macd_hist"]}')
        print(f'macd_signal: {data["macd_signal"]}')
        return data["macd"]


if __name__ == "__main__":
    test = get_finance_data.Info().alphavantage('IBM', "5min")
    #average = Metric().moving_average(a, 3)
    average = Metric.macd(test)
    Metric.display(test, average)

