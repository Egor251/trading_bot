import os
try:
    import configparser
except ImportError:
    import ConfigParser as configparser


def create_config(path):
    """
    Create a config file
    """
    config = configparser.ConfigParser()
    #config.add_section("Transactions")

    config['Account details'] = {'Account': ''}

    config['API keys'] = {'alphavantage': '347R98J0MSQOTQ6Z'}
    # alphavantage : 347R98J0MSQOTQ6Z

    config['Transactions'] = {'stopsteps': '10'}
    #config.set("Settings", "SMTP_server", "smtp.yandex.ru")
    #config.set("Settings", "SMTP_port", "465")
    #config.set("Settings", "POP3_server", "pop.yandex.com")
    #config.set("Settings", "POP3_port", "995")
    #

    with open(path, "w") as config_file:
        config.write(config_file)


if __name__ == '__main__':
    path = "settings.ini"
    if not os.path.exists(path):
        create_config(path)
    print(os.getcwd())
    create_config(path)
