import numpy
import matplotlib.dates as mdates
import matplotlib.pyplot as plt
import pandas as pd
from matplotlib.dates import MONDAY, DateFormatter, DayLocator, WeekdayLocator

import mplfinance
#from mplfinance import candlestick_ohlc
from mplfinance.original_flavor import candlestick_ohlc

import get_finance_data

def display(dataframe):

    mplfinance.plot(
            dataframe,
            type='candle',
            style='charles',
            ylabel='Price ($)',
            volume=True,
            ylabel_lower='Shares\nTraded',
            )

class Metric():
    pass

if __name__ == "__main__":
    test = get_finance_data.Info().alphavantage('IBM', "5min")
    display(test)
