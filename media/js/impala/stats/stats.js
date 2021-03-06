(function() {

    $(function() {
        "use strict";

        // Modify the URL when the page state changes, if the browser supports pushSate.
        if (z.capabilities.replaceState) {
            $(window).bind("changeview", function(e, view) {
                var queryParams = {},
                    range = view.range;
                if (range) {
                    if (typeof range == "string") {
                        queryParams.last = range.split(/\s+/)[0];
                    } else if (typeof range == "object") {
                        // queryParams.start = z.date.date_string(new Date(range.start), '');
                        // queryParams.end = z.date.date_string(new Date(range.end), '');
                    }
                }
                queryParams = $.param(queryParams);
                if (queryParams) {
                    history.replaceState(view, document.title, '?' + queryParams);
                }
            });
        }

        // Set up initial default view.
        var initView = {
            metric: $('.primary').attr('data-report'),
            range: $('.primary').attr('data-range') || '30 days',
            group: 'day'
        }

        // Restore any session view information from sessionStorage.
        if (z.capabilities.localStorage && sessionStorage.getItem('stats_view')) {
            var ssView = JSON.parse(sessionStorage.getItem('stats_view'));
            initView.range = ssView.range || initView.range;
            initView.group = ssView.group || initView.group;
        }

        // Update sessionStorage with our current view state.
        (function() {
            if (!z.capabilities.localStorage) return;
            var ssView = _.clone(initView);
            $(window).bind('changeview', function(e, newView) {
                _.extend(ssView, newView);
                sessionStorage.setItem('stats_view', JSON.stringify({
                    'range': ssView.range,
                    'group': ssView.group
                }));
            });
        })();

        // Update the "Export as CSV" link when the view changes.
        (function() {
            var view = {},
                baseURL = $(".primary").attr("data-base_url");
            $(window).bind('changeview', function(e, newView) {
                _.extend(view, newView);
                var metric = view.metric,
                    range = normalizeRange(view.range),
                    url = baseURL + ([metric,'day',range.start.pretty(''),range.end.pretty('')]).join('-') + '.csv';
                $('#export_data').attr('href', url);
            });
        })();

        // set up notes modal.
        $('#stats-note').modal("#stats-note-link", { width: 520 });

        // Trigger the initial data load.
        $(window).trigger('changeview', initView);
    });

    $('.csv-table').csvTable();
})();
