# PS4 MultiPut is a freestanding PIE launched through GoldHEN PayLoader.
# No runtime library imports: the receiver discovers ASLR-adjusted libkernel
# syscall stubs from its native thread-trampoline return address.

ifdef PS4_PAYLOAD_SDK
    include $(PS4_PAYLOAD_SDK)/toolchain/orbis.mk
else
    $(error PS4_PAYLOAD_SDK is undefined)
endif

ELF := multiput.elf
OBJ := build/multiput.o
SRC := src/multiput.c
CFLAGS := -O2 -fPIC -ffreestanding -fno-builtin -fno-stack-protector \
          -fno-asynchronous-unwind-tables -fno-unwind-tables -mstackrealign \
          -Wall -Wextra -Werror $(EXTRA_CFLAGS)

.PHONY: all check clean

all: $(ELF)

$(OBJ): $(SRC)
	mkdir -p build
	$(CC) $(CFLAGS) -c $< -o $@

$(ELF): $(OBJ)
	$(LD) -e _start -o $@ $(OBJ)

check: $(ELF)
	python3 multiput_inject.py --selftest
	python3 multiput_push.py --selftest

clean:
	-rm -rf build $(ELF)
