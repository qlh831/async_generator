from scheduler import sched
from selector import NonBlockingIO
from handler import router


def main():

    nbio = NonBlockingIO(sched)
    nbio.start_server("localhost", 8080, router)

    sched.schedule_gen(nbio.select())
    sched.run_forever()


if __name__ == "__main__":
    main()
