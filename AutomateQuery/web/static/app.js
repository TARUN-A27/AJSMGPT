(function () {
    function showOverlay(text) {
        var overlay = document.getElementById("loadingOverlay");
        var loadingText = document.getElementById("loadingText");
        if (loadingText) {
            loadingText.textContent = text || "Loading...";
        }
        if (overlay) {
            overlay.style.display = "flex";
        }
    }

    function hideOverlay() {
        var overlay = document.getElementById("loadingOverlay");
        if (overlay) {
            overlay.style.display = "none";
        }
    }

    function showToast(text) {
        var toast = document.getElementById("loadedToast");
        if (!toast) return;
        toast.textContent = text || "✓ Loaded";
        toast.classList.add("show");
        setTimeout(function () {
            toast.classList.remove("show");
        }, 1200);
    }

    function getFilterItems() {
        return Array.prototype.slice.call(document.querySelectorAll(".filter-item"));
    }

    function setActiveFilter(text) {
        var label = document.getElementById("activeFilterText");
        if (label) {
            label.textContent = text || "No filter";
        }
    }

    function clearFilter() {
        getFilterItems().forEach(function (item) {
            item.classList.remove("filter-hidden");
        });
        setActiveFilter("No filter");
        showToast("✓ Filter cleared");
    }

    function applyFilter(type, value, label) {
        var items = getFilterItems();
        var matched = 0;
        value = String(value || "").toLowerCase();

        items.forEach(function (item) {
            var isMatch = true;

            if (type === "category") {
                isMatch = String(item.dataset.category || "").toLowerCase() === value;
            } else if (type === "status") {
                isMatch = String(item.dataset.status || "").toLowerCase() === value;
            } else if (type === "problem") {
                isMatch = String(item.dataset.problems || "").toLowerCase().indexOf(value) !== -1;
            } else if (type === "fallback") {
                isMatch = String(item.dataset.fallback || "").toLowerCase() === value;
            } else if (type === "wrong-table") {
                isMatch = String(item.dataset.wrongTable || "").toLowerCase() === value;
            } else if (type === "review-required") {
                isMatch = String(item.dataset.reviewRequired || "").toLowerCase() === value;
            } else if (type === "text") {
                isMatch = String(item.dataset.search || item.textContent || "").toLowerCase().indexOf(value) !== -1;
            }

            item.classList.toggle("filter-hidden", !isMatch);
            if (isMatch) matched += 1;
        });

        setActiveFilter("Filter: " + label + " (" + matched + ")");
        showToast("✓ Filter applied");
    }

    window.addEventListener("load", function () {
        hideOverlay();
        showToast("✓ Loaded");
    });

    document.addEventListener("submit", function (event) {
        var form = event.target;
        var action = form.getAttribute("action") || "";

        if (action.indexOf("router-patch") !== -1) {
            showOverlay("Updating router patch review...");
        } else if (action.indexOf("run-approved-tests") !== -1) {
            showOverlay("Running approved eval tests...");
        } else if (action.indexOf("run-pipeline") !== -1) {
            showOverlay("Running automation pipeline...");
        } else if (action.indexOf("export-approved") !== -1) {
            showOverlay("Exporting approved tests...");
        } else if (action.indexOf("sync") !== -1) {
            showOverlay("Syncing review file...");
        } else if (action.indexOf("approve") !== -1) {
            showOverlay("Approving candidate...");
        } else if (action.indexOf("reject") !== -1) {
            showOverlay("Rejecting candidate...");
        } else if (action.indexOf("note") !== -1) {
            showOverlay("Saving note...");
        } else {
            showOverlay("Loading...");
        }
    });

    document.addEventListener("click", function (event) {
        var clearBtn = event.target.closest("[data-filter-clear]");
        if (clearBtn) {
            clearFilter();
            return;
        }

        var filterEl = event.target.closest("[data-filter-category], [data-filter-status], [data-filter-problem], [data-filter-fallback], [data-filter-wrong-table], [data-filter-review-required], [data-filter-text]");
        if (filterEl) {
            event.preventDefault();

            if (filterEl.dataset.filterCategory) {
                applyFilter("category", filterEl.dataset.filterCategory, filterEl.dataset.filterCategory);
                return;
            }

            if (filterEl.dataset.filterStatus) {
                applyFilter("status", filterEl.dataset.filterStatus, filterEl.textContent.trim());
                return;
            }

            if (filterEl.dataset.filterProblem) {
                applyFilter("problem", filterEl.dataset.filterProblem, filterEl.dataset.filterProblem);
                return;
            }

            if (filterEl.dataset.filterFallback) {
                applyFilter("fallback", filterEl.dataset.filterFallback, "Fallback Used");
                return;
            }

            if (filterEl.dataset.filterWrongTable) {
                applyFilter("wrong-table", filterEl.dataset.filterWrongTable, "Wrong Table Detected");
                return;
            }

            if (filterEl.dataset.filterReviewRequired) {
                applyFilter("review-required", filterEl.dataset.filterReviewRequired, "Review Required");
                return;
            }

            if (filterEl.dataset.filterText) {
                applyFilter("text", filterEl.dataset.filterText, filterEl.dataset.filterText);
                return;
            }
        }

        var link = event.target.closest("a");
        if (link && link.getAttribute("href") && !link.getAttribute("href").startsWith("#")) {
            showOverlay("Loading page...");
        }
    });
})();

(function () {
    function setText(parent, field, value) {
        var el = parent.querySelector('[data-field="' + field + '"]');
        if (!el) return;

        if (value === null || value === undefined || value === "") {
            el.textContent = "";
            return;
        }

        if (typeof value === "object") {
            el.textContent = JSON.stringify(value, null, 2);
            return;
        }

        el.textContent = String(value);
    }

    function setVerifyLoading(output, button, isLoading) {
        if (isLoading) {
            output.hidden = false;
            button.disabled = true;
            button.dataset.oldText = button.textContent;
            button.textContent = "Verifying...";
            setText(output, "success", "Loading...");
            setText(output, "source", "");
            setText(output, "intent", "");
            setText(output, "row_count", "");
            setText(output, "sql", "Running AJSMGPT verification. Please wait...");
            setText(output, "answer", "");
            return;
        }

        button.disabled = false;
        button.textContent = button.dataset.oldText || "Verify Query";
    }

    document.addEventListener("click", async function (event) {
        var button = event.target.closest(".ajax-verify-btn");
        if (!button) return;

        event.preventDefault();

        var url = button.dataset.verifyUrl;
        var outputId = button.dataset.outputId;
        var output = document.getElementById(outputId);

        if (!url || !output) return;

        setVerifyLoading(output, button, true);

        try {
            var response = await fetch(url, {
                method: "POST",
                headers: {
                    "Accept": "application/json"
                }
            });

            var data = await response.json();
            var result = data.result || {};

            output.hidden = false;

            setText(output, "success", result.success);
            setText(output, "source", result.source);
            setText(output, "intent", result.intent);
            setText(output, "row_count", result.row_count);
            setText(output, "sql", result.sql || "");
            setText(
                output,
                "answer",
                result.answer || result.error || data.message || "No answer text returned."
            );
        } catch (err) {
            output.hidden = false;
            setText(output, "success", "False");
            setText(output, "source", "");
            setText(output, "intent", "");
            setText(output, "row_count", "");
            setText(output, "sql", "");
            setText(output, "answer", "Verify request failed: " + err);
        } finally {
            setVerifyLoading(output, button, false);
        }
    });
})();

(function () {
    function setText(parent, field, value) {
        var el = parent.querySelector('[data-field="' + field + '"]');
        if (!el) return;

        if (value === null || value === undefined || value === "") {
            el.textContent = "";
            return;
        }

        if (typeof value === "object") {
            el.textContent = JSON.stringify(value, null, 2);
            return;
        }

        el.textContent = String(value);
    }

    document.addEventListener("click", async function (event) {
        var button = event.target.closest(".ajax-question-verify-btn");
        if (!button) return;

        event.preventDefault();

        var url = button.dataset.verifyUrl;
        var outputId = button.dataset.outputId;
        var output = document.getElementById(outputId);

        if (!url || !output) return;

        output.hidden = false;

        var oldText = button.textContent;
        button.disabled = true;
        button.textContent = "Verifying...";

        setText(output, "success", "Loading...");
        setText(output, "source", "");
        setText(output, "intent", "");
        setText(output, "row_count", "");
        setText(output, "sql", "Running AJSMGPT for this question...");
        setText(output, "answer", "");

        try {
            var response = await fetch(url, {
                method: "POST",
                headers: {
                    "Accept": "application/json"
                }
            });

            var data = await response.json();
            var result = data.result || {};

            setText(output, "success", result.success);
            setText(output, "source", result.source);
            setText(output, "intent", result.intent);
            setText(output, "row_count", result.row_count);
            setText(output, "sql", result.sql || "");
            setText(
                output,
                "answer",
                result.answer || result.error || data.message || "No answer text returned."
            );
        } catch (err) {
            setText(output, "success", "False");
            setText(output, "source", "");
            setText(output, "intent", "");
            setText(output, "row_count", "");
            setText(output, "sql", "");
            setText(output, "answer", "Verify request failed: " + err);
        } finally {
            button.disabled = false;
            button.textContent = oldText;
        }
    });
})();

/* Disable stuck full-page loading overlay.
   We now use button-level loading only, not page-blocking loading. */
(function () {
    function hideFullPageLoader() {
        var selectors = [
            "#loading-overlay",
            "#loadingOverlay",
            ".loading-overlay",
            ".page-loading",
            ".page-loader",
            ".loader-overlay"
        ];

        selectors.forEach(function (selector) {
            document.querySelectorAll(selector).forEach(function (el) {
                el.hidden = true;
                el.style.display = "none";
                el.style.visibility = "hidden";
                el.style.opacity = "0";
                el.classList.add("hidden");
            });
        });

        document.body.classList.remove("loading", "is-loading", "page-loading", "modal-open");
        document.documentElement.classList.remove("loading", "is-loading", "page-loading");
    }

    window.addEventListener("pageshow", hideFullPageLoader);
    window.addEventListener("load", hideFullPageLoader);
    document.addEventListener("DOMContentLoaded", hideFullPageLoader);

    document.addEventListener("submit", function () {
        setTimeout(hideFullPageLoader, 100);
        setTimeout(hideFullPageLoader, 1000);
        setTimeout(hideFullPageLoader, 3000);
    }, true);

    document.addEventListener("click", function () {
        setTimeout(hideFullPageLoader, 100);
        setTimeout(hideFullPageLoader, 1000);
    }, true);

    setInterval(hideFullPageLoader, 2000);
})();
