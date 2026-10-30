// uinput-inject — создаёт виртуальную мышь (uinput) и инжектит события из stdin.
// Протокол (по одной команде на строку):
//   m <dx> <dy>        — относительное перемещение
//   b <l|r|m> <0|1>    — кнопка (left/right/middle) вниз/вверх
//   w <dx> <dy>        — колесо (вертикаль/горизонталь)
//   q                  — выход
#include <fcntl.h>
#include <linux/input.h>
#include <linux/uinput.h>
#include <stdio.h>
#include <string.h>
#include <sys/ioctl.h>
#include <sys/time.h>
#include <unistd.h>

static int fd;

static void emit(int type, int code, int val) {
    struct input_event ev;
    memset(&ev, 0, sizeof(ev));
    gettimeofday(&ev.time, NULL);
    ev.type = type;
    ev.code = code;
    ev.value = val;
    if (write(fd, &ev, sizeof(ev)) < 0) { /* ignore */ }
}

int main(void) {
    fd = open("/dev/uinput", O_WRONLY | O_NONBLOCK);
    if (fd < 0) { perror("open /dev/uinput"); return 1; }

    ioctl(fd, UI_SET_EVBIT, EV_REL);
    ioctl(fd, UI_SET_EVBIT, EV_KEY);
    ioctl(fd, UI_SET_EVBIT, EV_SYN);
    ioctl(fd, UI_SET_RELBIT, REL_X);
    ioctl(fd, UI_SET_RELBIT, REL_Y);
    ioctl(fd, UI_SET_RELBIT, REL_WHEEL);
    ioctl(fd, UI_SET_RELBIT, REL_HWHEEL);
    ioctl(fd, UI_SET_KEYBIT, BTN_LEFT);
    ioctl(fd, UI_SET_KEYBIT, BTN_RIGHT);
    ioctl(fd, UI_SET_KEYBIT, BTN_MIDDLE);

    struct uinput_setup usetup;
    memset(&usetup, 0, sizeof(usetup));
    usetup.id.bustype = BUS_USB;
    usetup.id.vendor  = 0x1234;
    usetup.id.product = 0x5678;
    usetup.id.version = 1;
    strcpy(usetup.name, "second-monitor-touch");
    ioctl(fd, UI_DEV_SETUP, &usetup);
    ioctl(fd, UI_DEV_CREATE);

    char line[256];
    while (fgets(line, sizeof(line), stdin)) {
        int a = 0, b = 0, val = 0, code;
        char btn = 0;
        switch (line[0]) {
        case 'm':
            sscanf(line, "m %d %d", &a, &b);
            emit(EV_REL, REL_X, a);
            emit(EV_REL, REL_Y, b);
            emit(EV_SYN, SYN_REPORT, 0);
            break;
        case 'b':
            sscanf(line, "b %c %d", &btn, &val);
            code = (btn == 'r') ? BTN_RIGHT : (btn == 'm') ? BTN_MIDDLE : BTN_LEFT;
            emit(EV_KEY, code, val);
            emit(EV_SYN, SYN_REPORT, 0);
            break;
        case 'w':
            sscanf(line, "w %d %d", &a, &b);
            if (a) { emit(EV_REL, REL_WHEEL, a); }
            if (b) { emit(EV_REL, REL_HWHEEL, b); }
            emit(EV_SYN, SYN_REPORT, 0);
            break;
        case 'q':
            goto done;
        default:
            break;
        }
    }
done:
    ioctl(fd, UI_DEV_DESTROY);
    close(fd);
    return 0;
}
