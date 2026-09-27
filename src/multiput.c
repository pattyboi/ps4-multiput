/*
 * multiput.elf -- freestanding multi-stream receiver for PS4.
 *
 * GoldHEN maps this ELF without resolving libc or libkernel symbols.  The
 * payload therefore has no CRT, libc, PLT, GOT imports, pthreads, or fork.
 * GoldHEN starts the payload from a native libkernel thread trampoline.  The
 * payload scans backward from that return address, locates the required
 * libkernel syscall stubs, and calls through them to satisfy retail PS4
 * syscall-origin checks.
 *
 * One process multiplexes the listener and up to 64 DATA connections with
 * poll(2).  Each connection writes its disjoint range with pwrite(2), keeping
 * the adjacent multiput_protocol.py wire contract:
 *
 *   16-byte header: uint8 kind, 7 zero bytes, uint64 arg
 *   uint16 path length, path bytes
 *   DATA only: uint64 body length, body bytes
 *   reply: one byte, 'K' or 'E'
 */

#ifndef DEFAULT_PORT
#define DEFAULT_PORT 9022
#endif
#define MAX_CONNS 64
#define MAX_PATH_LEN 4096
#define RECV_CHUNK (256 * 1024)

#define KIND_SETUP 0
#define KIND_DATA 1

#define AF_INET 2
#define SOCK_STREAM 1
#define SOL_SOCKET 0xffff
#define SO_REUSEADDR 0x0004
#define SO_NOSIGPIPE 0x0800

#define O_WRONLY 0x0001
#define O_CREAT 0x0200

#define POLLIN 0x0001
#define POLLOUT 0x0004
#define POLLERR 0x0008
#define POLLHUP 0x0010
#define POLLNVAL 0x0020


typedef unsigned char u8;
typedef unsigned short u16;
typedef unsigned int u32;
typedef unsigned long u64;
typedef unsigned long usize;
typedef long ssize;

typedef struct {
  u8 len;
  u8 family;
  u16 port;
  u32 addr;
  u8 zero[8];
} sockaddr_in_t;

typedef struct {
  int fd;
  short events;
  short revents;
} pollfd_t;

typedef enum {
  ST_HEADER,
  ST_PATHLEN,
  ST_PATH,
  ST_LENGTH,
  ST_BODY,
  ST_REPLY,
} state_t;

typedef struct {
  int fd;
  int filefd;
  int active;
  state_t state;
  u8 field[16];
  usize field_have;
  usize field_want;
  u8 kind;
  u64 arg;
  u16 path_len;
  char path[MAX_PATH_LEN + 1];
  u64 body_length;
  u64 body_written;
  u8 reply_byte;
} conn_t;

static conn_t conns[MAX_CONNS];
static u8 recv_buf[RECV_CHUNK];
static pollfd_t pollfds[MAX_CONNS + 1];
static int pollmap[MAX_CONNS + 1];
typedef struct {
  u64 read_fn;
  u64 write_fn;
  u64 open_fn;
  u64 close_fn;
  u64 accept_fn;
  u64 socket_fn;
  u64 bind_fn;
  u64 setsockopt_fn;
  u64 listen_fn;
  u64 poll_fn;
  u64 pwrite_fn;
  u64 ftruncate_fn;
} syscall_api_t;

static volatile syscall_api_t syscall_api;

#define LIBKERNEL_PAGE_SIZE 0x4000
#define LIBKERNEL_SCAN_PAGES 32
#define REQUIRED_SYSCALLS 0x0fff

static void
record_syscall(u32 number, u64 address, u32 *found) {
  switch(number) {
    case 3: syscall_api.read_fn = address; *found |= 1U << 0; break;
    case 4: syscall_api.write_fn = address; *found |= 1U << 1; break;
    case 5: syscall_api.open_fn = address; *found |= 1U << 2; break;
    case 6: syscall_api.close_fn = address; *found |= 1U << 3; break;
    case 30: syscall_api.accept_fn = address; *found |= 1U << 4; break;
    case 97: syscall_api.socket_fn = address; *found |= 1U << 5; break;
    case 104: syscall_api.bind_fn = address; *found |= 1U << 6; break;
    case 105: syscall_api.setsockopt_fn = address; *found |= 1U << 7; break;
    case 106: syscall_api.listen_fn = address; *found |= 1U << 8; break;
    case 209: syscall_api.poll_fn = address; *found |= 1U << 9; break;
    case 476: syscall_api.pwrite_fn = address; *found |= 1U << 10; break;
    case 480: syscall_api.ftruncate_fn = address; *found |= 1U << 11; break;
    default: break;
  }
}

static int
resolve_syscalls(u64 caller) {
  const u8 *page =
      (const u8 *)(usize)(caller & ~((u64)LIBKERNEL_PAGE_SIZE - 1));
  u32 found = 0;

  for(int page_index = 0; page_index < LIBKERNEL_SCAN_PAGES; ++page_index) {
    for(usize offset = 0; offset <= LIBKERNEL_PAGE_SIZE - 12; ++offset) {
      const u8 *p = page + offset;
      if(p[0] != 0x48 || p[1] != 0xc7 || p[2] != 0xc0 ||
         p[7] != 0x49 || p[8] != 0x89 || p[9] != 0xca ||
         p[10] != 0x0f || p[11] != 0x05) {
        continue;
      }
      u32 number = (u32)p[3] | ((u32)p[4] << 8) |
                   ((u32)p[5] << 16) | ((u32)p[6] << 24);
      record_syscall(number, (u64)(usize)p, &found);
    }
    if(found == REQUIRED_SYSCALLS) {
      return 0;
    }
    page = (const u8 *)((usize)page - LIBKERNEL_PAGE_SIZE);
  }
  return -1;
}

#define CALL1(name, a1)                                                        \
  (((long (*)(long))(usize)syscall_api.name)((long)(a1)))
#define CALL2(name, a1, a2)                                                    \
  (((long (*)(long, long))(usize)syscall_api.name)((long)(a1), (long)(a2)))
#define CALL3(name, a1, a2, a3)                                                \
  (((long (*)(long, long, long))(usize)syscall_api.name)(                      \
      (long)(a1), (long)(a2), (long)(a3)))
#define CALL4(name, a1, a2, a3, a4)                                            \
  (((long (*)(long, long, long, long))(usize)syscall_api.name)(                \
      (long)(a1), (long)(a2), (long)(a3), (long)(a4)))
#define CALL5(name, a1, a2, a3, a4, a5)                                        \
  (((long (*)(long, long, long, long, long))(usize)syscall_api.name)(          \
      (long)(a1), (long)(a2), (long)(a3), (long)(a4), (long)(a5)))

static inline ssize
sys_read(int fd, void *buf, usize length) {
  return CALL3(read_fn, fd, buf, length);
}

static inline ssize
sys_write(int fd, const void *buf, usize length) {
  return CALL3(write_fn, fd, buf, length);
}

static inline int
sys_open(const char *path, int flags, int mode) {
  return (int)CALL3(open_fn, path, flags, mode);
}

static inline int
sys_close(int fd) {
  return (int)CALL1(close_fn, fd);
}

static inline int
sys_socket(int domain, int type, int protocol) {
  return (int)CALL3(socket_fn, domain, type, protocol);
}

static inline int
sys_bind(int fd, const void *addr, u32 length) {
  return (int)CALL3(bind_fn, fd, addr, length);
}

static inline int
sys_setsockopt(int fd, int level, int option, const void *value, u32 length) {
  return (int)CALL5(setsockopt_fn, fd, level, option, value, length);
}

static inline int
sys_listen(int fd, int backlog) {
  return (int)CALL2(listen_fn, fd, backlog);
}

static inline int
sys_accept(int fd) {
  return (int)CALL3(accept_fn, fd, 0, 0);
}

static inline int
sys_poll(pollfd_t *fds, u32 count, int timeout_ms) {
  return (int)CALL3(poll_fn, fds, count, timeout_ms);
}

static inline ssize
sys_pwrite(int fd, const void *buf, usize length, u64 offset) {
  return CALL4(pwrite_fn, fd, buf, length, offset);
}

static inline int
sys_ftruncate(int fd, u64 length) {
  return (int)CALL2(ftruncate_fn, fd, length);
}

static void
zero_bytes(void *ptr, usize length) {
  u8 *p = (u8 *)ptr;
  while(length--) {
    *p++ = 0;
  }
}

static u16
load_u16(const u8 *p) {
  return (u16)p[0] | ((u16)p[1] << 8);
}

static u64
load_u64(const u8 *p) {
  u64 value = 0;
  for(int i = 7; i >= 0; --i) {
    value = (value << 8) | p[i];
  }
  return value;
}

static u16
byte_swap_u16(u16 value) {
  return (u16)((value << 8) | (value >> 8));
}

static int
path_is_allowed(const char *path, u16 length) {
  static const char prefix[] = "/data/pkg/";
  const usize prefix_len = sizeof(prefix) - 1;

  if(length <= prefix_len || path[length - 1] == '/') {
    return 0;
  }
  for(usize i = 0; i < prefix_len; ++i) {
    if(path[i] != prefix[i]) {
      return 0;
    }
  }

  usize segment = prefix_len;
  for(usize i = prefix_len; i <= length; ++i) {
    if(i < length && path[i] == 0) {
      return 0;
    }
    if(i == length || path[i] == '/') {
      usize segment_len = i - segment;
      if(segment_len == 0 ||
         (segment_len == 1 && path[segment] == '.') ||
         (segment_len == 2 && path[segment] == '.' &&
          path[segment + 1] == '.')) {
        return 0;
      }
      segment = i + 1;
    }
  }
  return 1;
}

static void
conn_reset(conn_t *c) {
  if(c->filefd >= 0) {
    sys_close(c->filefd);
  }
  if(c->fd >= 0) {
    sys_close(c->fd);
  }
  zero_bytes(c, sizeof(*c));
  c->fd = -1;
  c->filefd = -1;
}

static conn_t *
conn_alloc(void) {
  for(int i = 0; i < MAX_CONNS; ++i) {
    if(!conns[i].active) {
      return &conns[i];
    }
  }
  return (conn_t *)0;
}

static void
conn_start_field(conn_t *c, state_t state, usize length) {
  c->state = state;
  c->field_have = 0;
  c->field_want = length;
}

static void
conn_fail(conn_t *c) {
  if(c->filefd >= 0) {
    sys_close(c->filefd);
    c->filefd = -1;
  }
  c->reply_byte = 'E';
  c->state = ST_REPLY;
}

static void
conn_finish_setup(conn_t *c) {
  int fd = sys_open(c->path, O_CREAT | O_WRONLY, 0666);
  if(fd < 0) {
    conn_fail(c);
    return;
  }
  int result = sys_ftruncate(fd, c->arg);
  sys_close(fd);
  if(result < 0) {
    conn_fail(c);
    return;
  }
  c->reply_byte = 'K';
  c->state = ST_REPLY;
}

static void
conn_open_data(conn_t *c) {
  c->filefd = sys_open(c->path, O_WRONLY, 0);
  if(c->filefd < 0) {
    conn_fail(c);
    return;
  }
  c->body_written = 0;
  c->state = ST_BODY;
}

static int
write_range(conn_t *c, const u8 *buf, usize length) {
  usize done = 0;
  while(done < length) {
    ssize n = sys_pwrite(c->filefd, buf + done, length - done,
                         c->arg + c->body_written + done);
    if(n < 0) {
      return -1;
    }
    if(n == 0) {
      return -1;
    }
    done += (usize)n;
  }
  return 0;
}

static void
conn_step(conn_t *c) {
  if(c->state == ST_REPLY) {
    (void)sys_write(c->fd, &c->reply_byte, 1);
    conn_reset(c);
    return;
  }

  if(c->state == ST_BODY) {
    u64 remaining = c->body_length - c->body_written;
    if(remaining == 0) {
      sys_close(c->filefd);
      c->filefd = -1;
      c->reply_byte = 'K';
      c->state = ST_REPLY;
      return;
    }

    usize want = remaining < RECV_CHUNK ? (usize)remaining : RECV_CHUNK;
    ssize n = sys_read(c->fd, recv_buf, want);
    if(n < 0) {
      conn_fail(c);
      return;
    }
    if(n == 0 || write_range(c, recv_buf, (usize)n) < 0) {
      conn_fail(c);
      return;
    }
    c->body_written += (u64)n;
    if(c->body_written == c->body_length) {
      sys_close(c->filefd);
      c->filefd = -1;
      c->reply_byte = 'K';
      c->state = ST_REPLY;
    }
    return;
  }

  u8 *field_target = c->state == ST_PATH ? (u8 *)c->path : c->field;
  ssize n = sys_read(c->fd, field_target + c->field_have,
                     c->field_want - c->field_have);
  if(n < 0) {
    conn_fail(c);
    return;
  }
  if(n == 0) {
    conn_fail(c);
    return;
  }
  c->field_have += (usize)n;
  if(c->field_have != c->field_want) {
    return;
  }

  switch(c->state) {
  case ST_HEADER:
    c->kind = c->field[0];
    c->arg = load_u64(c->field + 8);
    conn_start_field(c, ST_PATHLEN, 2);
    break;
  case ST_PATHLEN:
    c->path_len = load_u16(c->field);
    if(c->path_len == 0 || c->path_len > MAX_PATH_LEN) {
      conn_fail(c);
    } else {
      c->state = ST_PATH;
      c->field_have = 0;
      c->field_want = c->path_len;
    }
    break;
  case ST_PATH:
    c->path[c->path_len] = 0;
    if(!path_is_allowed(c->path, c->path_len)) {
      conn_fail(c);
    } else if(c->kind == KIND_SETUP) {
      conn_finish_setup(c);
    } else if(c->kind == KIND_DATA) {
      conn_start_field(c, ST_LENGTH, 8);
    } else {
      conn_fail(c);
    }
    break;
  case ST_LENGTH:
    c->body_length = load_u64(c->field);
    conn_open_data(c);
    break;
  default:
    conn_fail(c);
    break;
  }
}

static int
open_listener(u16 port) {
  int fd = sys_socket(AF_INET, SOCK_STREAM, 0);
  if(fd < 0) {
    return -1;
  }

  int one = 1;
  sys_setsockopt(fd, SOL_SOCKET, SO_REUSEADDR, &one, sizeof(one));
  sys_setsockopt(fd, SOL_SOCKET, SO_NOSIGPIPE, &one, sizeof(one));

  sockaddr_in_t addr;
  zero_bytes(&addr, sizeof(addr));
  addr.len = sizeof(addr);
  addr.family = AF_INET;
  addr.port = byte_swap_u16(port);

  if(sys_bind(fd, &addr, sizeof(addr)) < 0 || sys_listen(fd, MAX_CONNS) < 0) {
    sys_close(fd);
    return -1;
  }
  return fd;
}

static void
accept_one(int listener) {
  int fd = sys_accept(listener);
  if(fd < 0) {
    return;
  }

  conn_t *c = conn_alloc();
  if(!c) {
    sys_close(fd);
    return;
  }

  int one = 1;
  sys_setsockopt(fd, SOL_SOCKET, SO_NOSIGPIPE, &one, sizeof(one));
  zero_bytes(c, sizeof(*c));
  c->fd = fd;
  c->filefd = -1;
  c->active = 1;
  conn_start_field(c, ST_HEADER, 16);
}

static void
serve(void) {
  for(int i = 0; i < MAX_CONNS; ++i) {
    zero_bytes(&conns[i], sizeof(conns[i]));
    conns[i].fd = -1;
    conns[i].filefd = -1;
  }

  int listener = open_listener(DEFAULT_PORT);
  if(listener < 0) {
    return;
  }
  for(;;) {
    int count = 1;
    pollfds[0].fd = listener;
    pollfds[0].events = POLLIN;
    pollfds[0].revents = 0;
    pollmap[0] = -1;

    for(int i = 0; i < MAX_CONNS; ++i) {
      if(!conns[i].active) {
        continue;
      }
      pollfds[count].fd = conns[i].fd;
      pollfds[count].events = conns[i].state == ST_REPLY ? POLLOUT : POLLIN;
      pollfds[count].revents = 0;
      pollmap[count] = i;
      ++count;
    }

    int ready = sys_poll(pollfds, (u32)count, -1);
    if(ready < 0) {
      continue;
    }

    if(pollfds[0].revents & (POLLIN | POLLERR | POLLHUP | POLLNVAL)) {
      accept_one(listener);
    }

    for(int i = 1; i < count; ++i) {
      if(pollfds[i].revents & (POLLIN | POLLOUT | POLLERR | POLLHUP | POLLNVAL)) {
        int index = pollmap[i];
        if(index >= 0 && conns[index].active) {
          conn_step(&conns[index]);
        }
      }
    }
  }

  sys_close(listener);
}

void *
_start(void *unused) {
  (void)unused;
  if(resolve_syscalls((u64)(usize)__builtin_return_address(0)) < 0) {
    return (void *)0;
  }
  serve();
  return (void *)0;
}
