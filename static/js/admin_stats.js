// Script de la page admin/stats.html (Phase 8, point 2 : hors gabarit).
//
// Deux parties :
// 1. Tableau de bord : le fragment htmx #stats-insights décrit ses graphiques
//    en JSON dans des <canvas data-chart='…'>. On les dessine après chaque
//    échange (et on détruit les précédents).
// 2. Graphique personnalisé : chargé en JSON par fetch() (pas par htmx :
//    htmx échangeait la réponse en innerHTML, ce qui cassait JSON.parse dès
//    qu'un libellé contenait « < », et n'échangeait rien sur un 4xx). Il
//    reprend la période et les filtres du formulaire commun #stats-filters ;
//    la période normalisée par le serveur est lue dans #stats-range.

(function () {
    'use strict';

    var COLORS = {
        primary: '#0d6efd',
        warning: '#fd7e14',
        info: '#0aa2c0',
        waits: ['#198754', '#8bbf3f', '#fd7e14', '#dc3545']
    };

    var insightCharts = [];
    var chart = null;
    var controls = null;
    var requestSeq = 0;

    // ------------------------------------------------------------------
    // Utilitaires
    // ------------------------------------------------------------------

    function formatNumber(value, unit) {
        if (value === null || value === undefined || isNaN(Number(value))) return '—';
        var num = Number(value);
        if (unit === 'min') return num.toFixed(1).replace('.', ',') + ' min';
        if (Math.round(num) !== num) return num.toFixed(1).replace('.', ',');
        return String(num);
    }

    function setBox(id, message) {
        var box = document.getElementById(id);
        if (!box) return;
        box.textContent = message || '';
        box.classList.toggle('d-none', !message);
    }

    // ------------------------------------------------------------------
    // 1. Graphiques du tableau de bord
    // ------------------------------------------------------------------

    function destroyInsightCharts() {
        insightCharts.forEach(function (c) { c.destroy(); });
        insightCharts = [];
    }

    function insightConfig(spec) {
        var unit = spec.unit;
        var isDoughnut = spec.type === 'doughnut';
        var datasets = (spec.datasets || []).map(function (ds) {
            var color = spec.palette === 'waits' ? COLORS.waits : (COLORS[spec.color] || COLORS.primary);
            return {
                label: ds.label,
                data: ds.data,
                backgroundColor: color,
                borderColor: isDoughnut ? '#fff' : color,
                borderWidth: isDoughnut ? 2 : 0,
                borderRadius: isDoughnut ? 0 : 3
            };
        });
        var total = isDoughnut ? (spec.datasets[0].data || []).reduce(function (a, b) { return a + (Number(b) || 0); }, 0) : 0;

        var config = {
            type: spec.type,
            data: { labels: spec.labels, datasets: datasets },
            options: {
                responsive: true,
                maintainAspectRatio: false,
                plugins: {
                    legend: { display: isDoughnut, position: 'right' },
                    tooltip: {
                        callbacks: {
                            label: function (context) {
                                var text = formatNumber(context.raw, unit);
                                if (isDoughnut && total) {
                                    text += ' (' + Math.round(100 * context.raw / total) + ' %)';
                                }
                                return (context.dataset.label ? context.dataset.label + ' : ' : '') + text;
                            }
                        }
                    },
                    datalabels: isDoughnut ? {
                        color: '#fff',
                        font: { weight: 'bold' },
                        formatter: function (value) {
                            return total && value ? Math.round(100 * value / total) + ' %' : '';
                        }
                    } : { display: false }
                }
            }
        };
        if (!isDoughnut) {
            config.options.scales = {
                y: {
                    beginAtZero: true,
                    ticks: {
                        callback: function (value) { return unit === 'min' ? value + ' min' : value; }
                    }
                }
            };
        }
        return config;
    }

    function renderInsightCharts(root) {
        if (!window.Chart) return;
        destroyInsightCharts();
        root.querySelectorAll('canvas[data-chart]').forEach(function (canvas) {
            var spec;
            try {
                spec = JSON.parse(canvas.getAttribute('data-chart'));
            } catch (err) {
                console.error('Graphique illisible :', err);
                return;
            }
            insightCharts.push(new Chart(canvas.getContext('2d'), insightConfig(spec)));
        });
    }

    // ------------------------------------------------------------------
    // 2. Graphique personnalisé
    // ------------------------------------------------------------------

    function isTimeMetric() {
        var value = document.getElementById('data-selector').value;
        return value.indexOf('_times') !== -1;
    }

    // Un camembert de durées moyennes n'a pas de sens : option désactivée
    // (et remplacée par des barres) pour les métriques de temps.
    function syncExplorerControls() {
        var style = document.getElementById('chart-type-selector');
        var pie = style.querySelector('option[value="pie"]');
        var timeMetric = isTimeMetric();
        pie.disabled = timeMetric;
        if (timeMetric && style.value === 'pie') style.value = 'bar';
        document.getElementById('granularity-container')
            .classList.toggle('d-none', style.value !== 'line');
    }

    // Paramètres attendus par /admin/stats/chart, depuis le formulaire commun.
    function collectParams() {
        var params = new URLSearchParams();
        controls.querySelectorAll('select').forEach(function (el) {
            if (el.name && el.value) params.append(el.name, el.value);
        });

        var range = document.getElementById('stats-range');
        if (range && range.dataset.period === 'today') {
            params.append('date_type', 'current');
        } else if (range) {
            params.append('date_type', 'history');
            params.append('period_type', 'custom');
            params.append('start_date', range.dataset.start);
            params.append('end_date', range.dataset.end);
        }

        var filters = document.getElementById('stats-filters');
        if (filters) {
            filters.querySelectorAll('input[type="checkbox"]:checked').forEach(function (box) {
                params.append(box.name, box.value);
            });
        }
        return params;
    }

    function destroyChart() {
        if (chart) {
            chart.destroy();
            chart = null;
        }
    }

    function refreshChart() {
        if (!controls || !document.getElementById('stats-range')) return;
        // Un numero de sequence par requete : une reponse lente declenchee par
        // une selection precedente ne doit pas ecraser une reponse plus recente.
        var seq = ++requestSeq;
        var url = controls.dataset.chartUrl + '?' + collectParams().toString();

        fetch(url, {
            credentials: 'same-origin',
            headers: { 'X-Requested-With': 'XMLHttpRequest' }
        }).then(function (resp) {
            if (!resp.ok) throw new Error('HTTP ' + resp.status);
            return resp.json();
        }).then(function (data) {
            if (seq !== requestSeq) return;
            render(data);
        }).catch(function (err) {
            if (seq !== requestSeq) return;
            console.error('Chargement des statistiques :', err);
            destroyChart();
            setBox('chart-warning', null);
            setBox('chart-error', 'Impossible de charger le graphique. Réessayez.');
        });
    }

    function render(data) {
        if (data && data.error) {
            destroyChart();
            setBox('chart-warning', null);
            setBox('chart-error', data.error);
            return;
        }
        setBox('chart-error', null);
        setBox('chart-warning', data && data.warning);

        var canvas = document.getElementById('statsChart');
        if (!canvas || !window.Chart) return;
        destroyChart();
        chart = new Chart(canvas.getContext('2d'),
                          createChartConfig(document.getElementById('chart-type-selector').value, data));
    }

    // Somme des valeurs du premier jeu, pour les pourcentages (points {x, y}
    // en mode courbe).
    function datasetTotal(data) {
        var points = (data.datasets && data.datasets[0] && data.datasets[0].data) || [];
        return points.reduce(function (acc, point) {
            var value = (point && typeof point === 'object') ? point.y : point;
            return acc + (Number(value) || 0);
        }, 0);
    }

    function pointValue(raw) {
        return (raw && typeof raw === 'object') ? raw.y : raw;
    }

    function createChartConfig(chartType, data) {
        var total = datasetTotal(data);
        // Un pourcentage n'a de sens que pour une répartition (comptage).
        var withPercentage = !data.isTime && chartType !== 'line';
        var unit = data.isTime ? 'min' : '';

        function label(value, separator) {
            var text = formatNumber(Number(value) || 0, unit);
            if (withPercentage && total) {
                text += (separator || ' ') + '(' + ((Number(value) / total) * 100).toFixed(1).replace('.', ',') + ' %)';
            }
            return text;
        }

        var config = {
            type: chartType,
            data: data,
            options: {
                responsive: true,
                maintainAspectRatio: false,
                plugins: {
                    legend: { position: 'top' },
                    title: { display: true, text: data.title || '' },
                    tooltip: {
                        callbacks: {
                            label: function (context) {
                                var name = context.dataset.label || context.label || '';
                                return name + ' : ' + label(pointValue(context.raw));
                            }
                        }
                    },
                    datalabels: {
                        // Sur une courbe, les étiquettes de chaque point
                        // rendaient le graphique illisible.
                        display: chartType !== 'line',
                        formatter: function (value) { return label(pointValue(value), '\n'); }
                    }
                }
            }
        };

        var valueAxis = {
            beginAtZero: true,
            ticks: { callback: function (value) { return data.isTime ? value + ' min' : value; } }
        };

        if (chartType === 'pie') {
            config.options.plugins.datalabels.color = '#fff';
            config.options.plugins.datalabels.font = { weight: 'bold' };
        }
        if (chartType === 'bar') {
            config.options.plugins.datalabels.anchor = 'end';
            config.options.plugins.datalabels.align = 'top';
            config.options.scales = { y: valueAxis };
        }
        if (chartType === 'line') {
            config.options.scales = {
                x: {
                    type: 'time',
                    time: {
                        unit: document.getElementById('time-granularity').value,
                        displayFormats: { hour: 'DD/MM HH:mm', day: 'DD MMM' }
                    }
                },
                y: valueAxis
            };
        }
        return config;
    }

    // ------------------------------------------------------------------
    // Initialisation
    // ------------------------------------------------------------------

    document.addEventListener('DOMContentLoaded', function () {
        controls = document.getElementById('controls');
        if (window.Chart && window.ChartDataLabels) {
            Chart.register(ChartDataLabels);
        }
        if (window.moment) { moment.locale('fr'); }

        if (controls) {
            syncExplorerControls();
            controls.addEventListener('change', function () {
                syncExplorerControls();
                refreshChart();
            });
        }

        // Nouveau fragment de tableau de bord (période ou filtres changés) :
        // on redessine ses graphiques puis le graphique personnalisé, qui
        // dépend de la même période.
        document.body.addEventListener('htmx:afterSwap', function (evt) {
            var target = evt.detail && evt.detail.target;
            if (!target || target.id !== 'stats-insights') return;
            renderInsightCharts(target);
            refreshChart();
        });
    });
})();
