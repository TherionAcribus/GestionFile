// Script de la page admin/stats.html, extrait du gabarit (Phase 8, point 2).
// Charge en fin de page via le bloc `scripts_end` de admin/base.html : le
// navigateur peut le mettre en cache, et le gabarit redevient du HTML.
//
// Le chargement des donnees passe par fetch() et non par htmx : htmx echangeait
// la reponse JSON en innerHTML dans une <div>, ce qui cassait JSON.parse des
// qu'un libelle contenait « < » (et injectait du HTML dans la page), et
// n'echangeait rien du tout sur un statut 4xx -- le message de validation du
// serveur n'atteignait donc jamais l'utilisateur.

(function () {
    'use strict';

    var chart = null;
    var controls = null;
    var requestSeq = 0;
    var debounceTimer = null;

    // ------------------------------------------------------------------
    // Affichage conditionnel des controles
    // ------------------------------------------------------------------

    function toggleDateOptions() {
        var dateType = document.getElementById('date-type').value;
        var historyOptions = document.getElementById('history-options');
        var customDates = document.getElementById('custom-dates');
        var dayOfWeekFilter = document.getElementById('day-of-week-filter-container');

        if (dateType === 'history') {
            historyOptions.classList.remove('d-none');
            dayOfWeekFilter.classList.remove('d-none');
            toggleCustomDates();
        } else {
            historyOptions.classList.add('d-none');
            customDates.classList.add('d-none');
            dayOfWeekFilter.classList.add('d-none');
        }
    }

    function toggleCustomDates() {
        var periodType = document.getElementById('period-type').value;
        var customDates = document.getElementById('custom-dates');
        customDates.classList.toggle('d-none', periodType !== 'custom');
    }

    function toggleGranularity() {
        var container = document.getElementById('granularity-container');
        var isLine = document.getElementById('chart-type-selector').value === 'line';
        container.style.display = isLine ? 'block' : 'none';
    }

    // ------------------------------------------------------------------
    // Encarts de message
    // ------------------------------------------------------------------

    function setBox(id, message) {
        var box = document.getElementById(id);
        if (!box) return;
        box.textContent = message || '';
        box.classList.toggle('d-none', !message);
    }

    function destroyChart() {
        if (chart) {
            chart.destroy();
            chart = null;
        }
    }

    // ------------------------------------------------------------------
    // Chargement
    // ------------------------------------------------------------------

    // Serialise les controles. Les <select multiple> produisent une entree par
    // option selectionnee (l'API attend des listes repetees).
    function collectParams() {
        var params = new URLSearchParams();
        controls.querySelectorAll('select, input').forEach(function (el) {
            if (!el.name || el.disabled) return;
            if (el.multiple) {
                Array.prototype.forEach.call(el.selectedOptions, function (opt) {
                    params.append(el.name, opt.value);
                });
            } else if (el.value !== '') {
                params.append(el.name, el.value);
            }
        });
        return params;
    }

    function refreshChart() {
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
            setBox('chart-error', 'Impossible de charger les statistiques. Reessayez.');
        });
    }

    function render(data) {
        // Parametre refuse par la validation serveur (type inconnu, periode trop
        // longue pour la granularite demandee...).
        if (data && data.error) {
            destroyChart();
            setBox('chart-warning', null);
            setBox('chart-error', data.error);
            return;
        }
        setBox('chart-error', null);
        // Filtre que les donnees archivees ne peuvent pas honorer : le
        // graphique est correct mais partiel, on le dit.
        setBox('chart-warning', data && data.warning);

        var canvas = document.getElementById('statsChart');
        if (!canvas) return;

        destroyChart();
        chart = new Chart(canvas.getContext('2d'),
                          createChartConfig(document.getElementById('chart-type-selector').value,
                                            data));
    }

    // ------------------------------------------------------------------
    // Configuration Chart.js
    // ------------------------------------------------------------------

    // Somme des valeurs du premier jeu, pour les pourcentages. En mode `line`
    // les points sont des objets {x, y} : sommer les points bruts produisait une
    // concatenation de chaines, et `context.raw.toFixed()` levait une TypeError
    // dans l'infobulle.
    function datasetTotal(data) {
        var points = (data.datasets && data.datasets[0] && data.datasets[0].data) || [];
        return points.reduce(function (acc, point) {
            var value = (point && typeof point === 'object') ? point.y : point;
            return acc + (Number(value) || 0);
        }, 0);
    }

    // Un pourcentage n'a de sens que pour une repartition (comptage), pas pour
    // une moyenne de duree, ni sur une serie temporelle.
    function showsPercentage(chartType, data) {
        return !data.isTime && chartType !== 'line';
    }

    function pointValue(raw) {
        return (raw && typeof raw === 'object') ? raw.y : raw;
    }

    function formatValue(value, isTime) {
        var num = Number(value) || 0;
        return isTime ? num.toFixed(1) + ' min' : String(num);
    }

    function createChartConfig(chartType, data) {
        var total = datasetTotal(data);
        var withPercentage = showsPercentage(chartType, data);

        function label(value, separateur) {
            var text = formatValue(value, data.isTime);
            if (withPercentage && total) {
                text += (separateur || ' ') +
                        '(' + ((Number(value) / total) * 100).toFixed(1) + '%)';
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
                                // Sur une courbe, c'est le nom de la serie qui
                                // identifie le point ; sur un camembert, le
                                // libelle de la part (les jeux n'y ont pas de
                                // nom).
                                var name = context.dataset.label || context.label || '';
                                return name + ': ' + label(pointValue(context.raw));
                            }
                        }
                    },
                    datalabels: {
                        // Le pourcentage passe a la ligne : l'etiquette est
                        // dessinee dans la part / au-dessus de la barre.
                        formatter: function (value) {
                            return label(pointValue(value), '\n');
                        }
                    }
                }
            }
        };

        var valueAxis = {
            beginAtZero: true,
            ticks: {
                callback: function (value) {
                    return data.isTime ? value + ' min' : value;
                }
            }
        };

        if (chartType === 'pie' || chartType === 'doughnut') {
            config.options.plugins.datalabels.color = '#fff';
            config.options.plugins.datalabels.font = { weight: 'bold' };
        }

        if (chartType === 'bar') {
            config.options.plugins.datalabels.anchor = 'end';
            config.options.plugins.datalabels.align = 'top';
            config.options.scales = { y: valueAxis };
        }

        if (chartType === 'line') {
            config.options.plugins.datalabels.align = 'top';
            config.options.scales = {
                x: {
                    type: 'time',
                    time: {
                        unit: document.getElementById('time-granularity').value,
                        displayFormats: { hour: 'HH:mm', day: 'DD MMM' }
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
        if (!controls) return;

        if (window.Chart && window.ChartDataLabels) {
            Chart.register(ChartDataLabels);
        }

        toggleDateOptions();
        toggleGranularity();

        // Un seul ecouteur delegue : les <select> rechargent immediatement, les
        // champs date attendent 400 ms (sans quoi chaque frappe dans un
        // <input type="date"> declenchait une requete complete).
        controls.addEventListener('change', function (evt) {
            var target = evt.target;
            if (target.id === 'date-type') toggleDateOptions();
            if (target.id === 'period-type') toggleCustomDates();
            if (target.id === 'chart-type-selector') toggleGranularity();

            clearTimeout(debounceTimer);
            if (target.classList.contains('date-input')) {
                debounceTimer = setTimeout(refreshChart, 400);
            } else {
                refreshChart();
            }
        });

        refreshChart();
    });
})();
