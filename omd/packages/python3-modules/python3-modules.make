include $(REPO_PATH)/defines.make

PYTHON3_MODULES := python3-modules

PYTHON3_MODULES_INSTALL_DIR := $(INTERMEDIATE_INSTALL_BASE)/$(PYTHON3_MODULES)
PYTHON3_MODULES_BUILD_DIR := $(BAZEL_BIN)/omd/packages/$(PYTHON3_MODULES)/$(PYTHON3_MODULES)

PYTHON3_MODULES_BUILD := $(BUILD_HELPER_DIR)/$(PYTHON3_MODULES)-build
PYTHON3_MODULES_INTERMEDIATE_INSTALL := $(BUILD_HELPER_DIR)/$(PYTHON3_MODULES)-install-intermediate
PYTHON3_MODULES_INSTALL := $(BUILD_HELPER_DIR)/$(PYTHON3_MODULES)-install

PACKAGE_PYTHON3_MODULES_PYTHON_DEPS := $(OPENSSL_CACHE_PKG_PROCESS) $(PYTHON_CACHE_PKG_PROCESS) $(PYTHON3_MODULES_CACHE_PKG_PROCESS)

# Used by other OMD packages
PACKAGE_PYTHON3_MODULES_DESTDIR    := $(PYTHON3_MODULES_INSTALL_DIR)
PACKAGE_PYTHON3_MODULES_PYTHONPATH := $(PACKAGE_PYTHON3_MODULES_DESTDIR)/lib/python$(PYTHON_MAJOR_DOT_MINOR)/site-packages
# May be used during omd package build time. Call sites have to use the target
# dependency "$(PACKAGE_PYTHON3_MODULES_PYTHON_DEPS)" to have everything needed in place.
PACKAGE_PYTHON3_MODULES_PYTHON         := \
	PYTHONPATH="$$PYTHONPATH:$(PACKAGE_PYTHON3_MODULES_PYTHONPATH):$(PACKAGE_PYTHON_PYTHONPATH)" \
	LDFLAGS="$$LDFLAGS $(PACKAGE_PYTHON_LDFLAGS)" \
	LD_LIBRARY_PATH="$$LD_LIBRARY_PATH:$(PACKAGE_PYTHON_LD_LIBRARY_PATH):$(PACKAGE_OPENSSL_LD_LIBRARY_PATH)" \
	$(PACKAGE_PYTHON_EXECUTABLE)

# on Sles Distros we temporarily need to deactivate SSL checking
ifneq ($(filter $(DISTRO_CODE),sles15 sles15sp1 sles15sp2 sles15sp3 sles15sp4),)
	OPTIONAL_BUILD_ARGS := BAZEL_EXTRA_ARGS="--define git-ssl-no-verify=true"
endif

$(PYTHON3_MODULES_BUILD):
	$(OPTIONAL_BUILD_ARGS) $(BAZEL_BUILD) //omd/packages/python3-modules:python3-modules-modify

$(PYTHON3_MODULES_INTERMEDIATE_INSTALL): $(PYTHON3_MODULES_BUILD)
	$(RSYNC) --times $(PYTHON3_MODULES_BUILD_DIR)/ $(PYTHON3_MODULES_INSTALL_DIR)/
	# TODO: Investigate why this fix-up is needed
	chmod +x $(PYTHON3_MODULES_INSTALL_DIR)/bin/*

$(PYTHON3_MODULES_INSTALL): $(PYTHON3_MODULES_INTERMEDIATE_INSTALL)
	$(PACKAGE_PYTHON_EXECUTABLE) -m compileall \
	    -f \
	    --invalidation-mode=checked-hash \
	    -s "$(PACKAGE_PYTHON3_MODULES_PYTHONPATH)/" \
	    -o 0 -o 1 -o 2 -j0 \
	    "$(PACKAGE_PYTHON3_MODULES_PYTHONPATH)/"
	$(RSYNC) --times $(PYTHON3_MODULES_INSTALL_DIR)/ $(DESTDIR)$(OMD_ROOT)/

