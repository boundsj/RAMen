// Original bounded Wayland fixture: deliberately slow text, never more than 1 MiB.
#include <wayland-client.h>
#include "data-control.h"
#include <errno.h>
#include <signal.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>
#include <sys/wait.h>
static struct wl_seat *seat;
static struct ext_data_control_manager_v1 *manager;
static struct ext_data_control_device_v1 *device;
static int hold_ms;
static int bytes;
static void send_data(void *unused, struct ext_data_control_source_v1 *source, const char *mime, int fd) {
    fprintf(stderr, "RECEIVE %s\n", mime); fflush(stderr);
    pid_t child = fork();
    if (child == 0) {
        signal(SIGPIPE, SIG_IGN);
        if (hold_ms) usleep(hold_ms * 1000);
        char block[4096]; memset(block, 'x', sizeof block);
        int left = bytes;
        while (left > 0) {
            ssize_t n = write(fd, block, left < (int)sizeof block ? left : sizeof block);
            if (n <= 0) break;
            left -= n;
        }
        close(fd); _exit(0);
    }
    close(fd);
}
static void cancelled(void *unused, struct ext_data_control_source_v1 *source) { }
static const struct ext_data_control_source_v1_listener source_listener = {send_data, cancelled};
static void mime(void *unused, struct ext_data_control_offer_v1 *o, const char *type) { }
static const struct ext_data_control_offer_v1_listener offer_listener = {mime};
static void offer(void *unused, struct ext_data_control_device_v1 *d, struct ext_data_control_offer_v1 *o) { ext_data_control_offer_v1_add_listener(o, &offer_listener, NULL); }
static void selection(void *unused, struct ext_data_control_device_v1 *d, struct ext_data_control_offer_v1 *o) { }
static void finished(void *unused, struct ext_data_control_device_v1 *d) { }
static const struct ext_data_control_device_v1_listener device_listener = {offer, selection, finished, selection};
static void global(void *unused, struct wl_registry *registry, uint32_t id, const char *name, uint32_t version) {
    if (!strcmp(name, "wl_seat")) seat = wl_registry_bind(registry, id, &wl_seat_interface, 1);
    if (!strcmp(name, "ext_data_control_manager_v1")) manager = wl_registry_bind(registry, id, &ext_data_control_manager_v1_interface, 1);
}
static void removed(void *unused, struct wl_registry *r, uint32_t id) { }
static const struct wl_registry_listener registry_listener = {global, removed};
int main(int argc, char **argv) {
    if (argc != 3) return 2;
    hold_ms = atoi(argv[1]); bytes = atoi(argv[2]);
    if (hold_ms < 0 || hold_ms > 5000 || bytes < 1 || bytes > 1048576) return 2;
    signal(SIGCHLD, SIG_IGN); alarm(12);
    struct wl_display *display = wl_display_connect(NULL);
    if (!display) return 3;
    struct wl_registry *registry = wl_display_get_registry(display);
    wl_registry_add_listener(registry, &registry_listener, NULL);
    if (wl_display_roundtrip(display) < 0 || !manager || !seat) return 4;
    device = ext_data_control_manager_v1_get_data_device(manager, seat);
    ext_data_control_device_v1_add_listener(device, &device_listener, NULL);
    struct ext_data_control_source_v1 *source = ext_data_control_manager_v1_create_data_source(manager);
    ext_data_control_source_v1_add_listener(source, &source_listener, NULL);
    ext_data_control_source_v1_offer(source, "text/plain;charset=utf-8");
    ext_data_control_source_v1_offer(source, "text/plain");
    ext_data_control_device_v1_set_selection(device, source);
    wl_display_flush(display);
    fprintf(stderr, "READY\n"); fflush(stderr);
    while (wl_display_dispatch(display) >= 0) { }
    return 0;
}
