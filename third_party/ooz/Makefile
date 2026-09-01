# Oodle Kraken decompressor for reading Commander Quest's pak.
# Upstream sources (kraken.cpp, lzna.cpp, bitknit.cpp) are vendored pristine from
# powzix/ooz (GPL-3.0). This Makefile applies the two changes needed on Linux:
#   1. drop kraken.cpp's Windows-only tail (oo2core DLL loading + main())
#   2. swap stdafx.h for ooz_linux.h, which shims the MSVC builtins
CXX      ?= g++
CXXFLAGS ?= -O2 -fPIC -msse4.1 -w
GEN       = kraken_lib.cpp lzna_lib.cpp bitknit_lib.cpp

libooz.so: $(GEN) ooz_export.cpp ooz_linux.h
	$(CXX) $(CXXFLAGS) -shared -o $@ $(GEN) ooz_export.cpp

# Cut at the first Windows-only declaration rather than a hardcoded line number.
kraken_lib.cpp: kraken.cpp
	awk '/^typedef int WINAPI OodLZ_CompressFunc\(/{exit} {print}' $< \
	  | sed 's|#include "stdafx.h"|#include "ooz_linux.h"|' > $@

%_lib.cpp: %.cpp
	sed 's|#include "stdafx.h"|#include "ooz_linux.h"|' $< > $@

clean:
	rm -f libooz.so $(GEN)
.PHONY: clean
