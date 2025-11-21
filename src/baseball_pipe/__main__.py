import baseball_pipe.web_server

def main():
    ws = baseball_pipe.web_server.WebServer()
    ws.start()

if __name__ == '__main__':
    main()