

class Optimizer():

    def optimize(self, file, optimize_range=range(1, 10)):
        with open(file) as func:
            parse_parameter = []
            output = []
            for line in func:
                #print(line)
                if line.find('optimization_parameters') != -1:
                    tmp = line.split('[')
                    parse_parameter = tmp[1].replace(']', '').replace("'", '').replace('\n', '').split(',')
                for parameter in parse_parameter:
                    if line.find(parameter + ' ') != -1:
                        output.append(line.split(' ')[-1].replace('\n', ''))
            print(len(output))
            print(output)
            for param in output:
                for i in optimize_range:
                    for line in func:
                        # print(line)
                        if line.find(param + ' ') != -1:
                            print(line)





if __name__ == '__main__':
    file = 'strategies/test_strategy.py'
    Optimizer().optimize(file)
