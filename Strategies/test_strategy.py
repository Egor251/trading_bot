from structy import Strat


class Strategy(Strat):  # main class must be named "Strategy"

    # All necessary variables
    algorithm = 'Test'  # Short description of strategies type
    description = 'Its a test strategy, it shows how strategy should looks like'  # Full description of strategy
    optimization_parameters = {'a': range(5), 'def': range(10)}  # В файле со стратегией должна быть переменная optimization_parameters содержащий словарь типа {переменная: range(x, y)} где range это диапазон значений для оптимизации
    default = [100, 120]  # default strategy parameters
    #class_name = 'Test'
    #def_name = 'test'

    def run(self, param):  # main func must be named "run"
        if len(param) < len(self.optimization_parameters):
            param = self.default
        a = param[0]
        default = param[1]
        #print(a, default)
        return a * default

    def test(self):
        #print(eval(f"{self.driver}().test()"))
        pass


if __name__ == '__main__':
    #Strategy().run([1, 5])
    #print(Strategy().get_attr('optimization_parameters'))
    Strategy().test()
    pass
