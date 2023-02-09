import itertools
import json
import runpy


class Optimizer():

    def optimize(self, file, optimize_range=range(1, 10)):
        with open(file) as func:
            parse_parameter = []
            output = []
            for line in func:
                #print(line)
                if line.find('optimization_parameters') != -1:
                    tmp = line.split('=')
                    tmp = tmp[-1].replace(' ', '')
                    parse_parameter = eval(tmp)
                    print(parse_parameter)
            print(len(output))
            print(output)
            param = itertools.permutations(optimize_range, int(len(parse_parameter)))
            runpy.run_path(file, run_name='__main__')
            '''for par in param:
                print(par)'''





if __name__ == '__main__':
    file = 'strategies/test_strategy.py'
    Optimizer().optimize(file)
