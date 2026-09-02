// Script de la page admin/stats.html, extrait du gabarit (Phase 8, point 2).
// Charge en fin de page via le bloc `scripts_end` de admin/base.html : le
// navigateur peut le mettre en cache, et le gabarit redevient du HTML.

function toggleDateOptions() {
    const dateType = document.getElementById('date-type').value;
    const historyOptions = document.getElementById('history-options');
    const customDates = document.getElementById('custom-dates');
    const dayOfWeekFilter = document.getElementById('day-of-week-filter-container');
        
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
    const periodType = document.getElementById('period-type').value;
    const customDates = document.getElementById('custom-dates');
        
    if (periodType === 'custom') {
        customDates.classList.remove('d-none');
    } else {
        customDates.classList.add('d-none');
    }
}

// Appeler toggleDateOptions au chargement de la page
document.addEventListener('DOMContentLoaded', function() {
    toggleDateOptions();
});

let chart;
const granularityContainer = document.getElementById('granularity-container');
    
// Mise à jour de l'affichage des contrôles en fonction des sélections
document.getElementById('chart-type-selector').addEventListener('change', function(e) {
    const isLine = e.target.value === 'line';
    granularityContainer.style.display = isLine ? 'block' : 'none';
});

// Logique de mise à jour du graphique
htmx.on('#chart-data', 'htmx:afterSettle', function(evt) {
    const canvas = document.getElementById('statsChart');
    if (!canvas) return;
        
    const ctx = canvas.getContext('2d');
    const errorBox = document.getElementById('chart-error');
    let data;
    try {
        data = JSON.parse(evt.detail.elt.textContent);
    } catch (error) {
        console.error('Error parsing JSON:', error);
        return;
    }

    // Réponse d'erreur validée côté serveur (paramètre invalide, période
    // trop longue pour la granularité…) : on l'affiche au lieu de planter.
    if (data && data.error) {
        if (chart) { chart.destroy(); chart = null; }
        if (errorBox) {
            errorBox.textContent = data.error;
            errorBox.classList.remove('d-none');
        }
        return;
    }
    if (errorBox) { errorBox.textContent = ''; errorBox.classList.add('d-none'); }

    if (chart) {
        chart.destroy();
    }

    const chartType = document.getElementById('chart-type-selector').value;
    const config = createChartConfig(chartType, data);
    chart = new Chart(ctx, config);
});

function createChartConfig(chartType, data) {
    // Calculer le total pour les pourcentages
    const total = data.datasets[0].data.reduce((a, b) => a + b, 0);
        
    const config = {
        type: chartType,
        data: data,
        options: {
            responsive: true,
            maintainAspectRatio: false,
            plugins: {
                legend: {
                    position: 'top',
                },
                title: {
                    display: true,
                    text: data.title || ''
                },
                tooltip: {
                    callbacks: {
                        label: function(context) {
                            const value = context.raw;
                            const percentage = ((value / total) * 100).toFixed(1);
                                
                            if (data.isTime) {
                                // Pour les temps, on affiche en minutes
                                return `${context.label}: ${value.toFixed(1)} min (${percentage}%)`;
                            } else {
                                // Pour les comptages
                                return `${context.label}: ${value} (${percentage}%)`;
                            }
                        }
                    }
                }
            }
        }
    };

    if (chartType === 'pie' || chartType === 'doughnut') {
        config.options.plugins.datalabels = {
            color: '#fff',
            font: {
                weight: 'bold'
            },
            formatter: function(value, context) {
                const percentage = ((value / total) * 100).toFixed(1);
                return `${value}\n(${percentage}%)`;
            }
        };
    }
        
    if (chartType === 'bar') {
        config.options.plugins.datalabels = {
            anchor: 'end',
            align: 'top',
            formatter: function(value, context) {
                const percentage = ((value / total) * 100).toFixed(1);
                return `${value}\n(${percentage}%)`;
            }
        };
        config.options.scales = {
            y: {
                beginAtZero: true,
                ticks: {
                    callback: function(value) {
                        if (data.isTime) {
                            return `${value} min`;
                        }
                        return value;
                    }
                }
            }
        };
    }

    if (chartType === 'line') {
        config.options.scales = {
            x: {
                type: 'time',
                time: {
                    unit: document.getElementById('time-granularity').value,
                    displayFormats: {
                        hour: 'HH:mm',
                        day: 'DD MMM'
                    }
                }
            },
            y: {
                beginAtZero: true,
                ticks: {
                    callback: function(value) {
                        if (data.isTime) {
                            return `${value} min`;
                        }
                        return value;
                    }
                }
            }
        };
        config.options.plugins.datalabels = {
            align: 'top',
            formatter: function(value, context) {
                if (data.isTime) {
                    return `${value.y.toFixed(1)} min`;
                }
                return value.y;
            }
        };
    }

    return config;
}


// Ajout avant la création du graphique
document.addEventListener('DOMContentLoaded', function() {
    import('https://cdnjs.cloudflare.com/ajax/libs/chartjs-plugin-datalabels/2.2.0/chartjs-plugin-datalabels.min.js')
        .then(() => {
            Chart.register(ChartDataLabels);
        })
        .catch(err => console.error('Erreur chargement plugin datalabels:', err));
});


// Initialisation
document.addEventListener('DOMContentLoaded', function() {
    // Déclencher le premier chargement
    document.getElementById('data-selector').dispatchEvent(new Event('change'));
        
    // Initialiser l'affichage des contrôles
    granularityContainer.style.display = 
        document.getElementById('chart-type-selector').value === 'line' 
        ? 'block' 
        : 'none';
});
