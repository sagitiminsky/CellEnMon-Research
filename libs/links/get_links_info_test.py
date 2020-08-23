import unittest
from libs.links.get_stock_info import GetStocksInfo
from unittest.mock import MagicMock
import config


class StocksTest(unittest.TestCase):
    def setUp(self):
        self.stock_names= config.stock_names

    def test_create_GetStockInfo(self):
        """
        Test libs/get_stock_info.py

        Creates an GetStockInfo object, and test it creation of obejct is successful
        """
        self.assertTrue(GetStocksInfo(mock=MagicMock())) #magic mock for graph_objj


    def test_measure_stock(self):
        """
        Test libs/get_stock_info.py
        - preform test_create_GetStockInfo
        - Use mock to imitate reading from stock prices DB and test measure_stock
        """

        stockObj=GetStocksInfo(mock=MagicMock()) #magic mock for graph_objj
        self.assertTrue(stockObj.measure(mock=MagicMock())) #magic mock for graph_objj


if __name__ == '__main__':
    unittest.main()


