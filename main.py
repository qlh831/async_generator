from scheduler import sched
from selector import NonBlockingIO
from handler import router


def main():
    import random
    port = random.randint(8000, 9000)

    nbio = NonBlockingIO(sched)
    nbio.start_server("localhost", port, router)

    sched.schedule_gen(nbio.select())
    sched.run_forever()


if __name__ == "__main__":
    main()
