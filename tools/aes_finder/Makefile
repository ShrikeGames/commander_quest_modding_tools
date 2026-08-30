CC ?= gcc
CFLAGS ?= -O2 -w
aes_finder: aes_finder.c
	$(CC) $(CFLAGS) -o $@ $< -lcrypto
clean:
	rm -f aes_finder
.PHONY: clean
