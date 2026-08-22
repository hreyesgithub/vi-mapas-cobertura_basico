# /utilidades/simulador.py
import random
import math
from collections import defaultdict

class SimuladorRedes:
    def __init__(self, escenario):
        """
        escenario: dict con:
            - num_aps: int (número de puntos de acceso)
            - capacidad_ap: int (Mbps que soporta cada AP)
            - num_usuarios: int
            - tipo_trafico: 'streaming', 'navegacion', 'mixto'
            - tiempo_simulacion: int (pasos de simulación)
            - fallo_ap: int (índice del AP que falla) o None
        """
        self.num_aps = escenario.get('num_aps', 2)
        self.capacidad_ap = escenario.get('capacidad_ap', 450)  # Mbps
        self.num_usuarios = escenario.get('num_usuarios', 50)
        self.tipo_trafico = escenario.get('tipo_trafico', 'mixto')
        self.tiempo_simulacion = escenario.get('tiempo_simulacion', 50)
        self.fallo_ap = escenario.get('fallo_ap', None)

        # Asignación inicial de APs a usuarios (distribución aleatoria)
        self.asignacion_aps = [random.randint(0, self.num_aps - 1) for _ in range(self.num_usuarios)]

        # Perfiles de tráfico por usuario (Mbps)
        self.perfiles_trafico = self._generar_perfiles_trafico()

        # Historial de carga por AP
        self.historial_carga = {ap: [] for ap in range(self.num_aps)}
        self.alertas = []

    def _generar_perfiles_trafico(self):
        perfiles = []
        for _ in range(self.num_usuarios):
            if self.tipo_trafico == 'streaming':
                # Tráfico constante y alto (5-10 Mbps)
                base = random.uniform(5, 10)
            elif self.tipo_trafico == 'navegacion':
                # Tráfico bajo y variable (0.5-2 Mbps)
                base = random.uniform(0.5, 2)
            else:  # mixto
                base = random.uniform(1, 8)
            perfiles.append(base)
        return perfiles

    def simular_paso(self, paso):
        """Simula un paso de tiempo"""
        # 1. Generar tráfico instantáneo para cada usuario
        trafico_instantaneo = []
        for i in range(self.num_usuarios):
            # Variabilidad realista (picos)
            factor = random.uniform(0.5, 1.5)
            trafico = self.perfiles_trafico[i] * factor
            trafico_instantaneo.append(trafico)

        # 2. Calcular carga por AP
        carga_aps = defaultdict(float)
        for i, usuario in enumerate(trafico_instantaneo):
            ap_id = self.asignacion_aps[i]
            # Si el AP ha fallado (por el escenario de fallo), reasignar
            if self.fallo_ap is not None and ap_id == self.fallo_ap:
                # Reasignar al AP con menor carga actual
                nuevas_cargas = {ap: carga_aps[ap] for ap in range(self.num_aps) if ap != self.fallo_ap}
                if nuevas_cargas:
                    nuevo_ap = min(nuevas_cargas, key=nuevas_cargas.get) # type: ignore
                    ap_id = nuevo_ap
                    self.asignacion_aps[i] = nuevo_ap  # Reasignar permanentemente

            carga_aps[ap_id] += usuario

        # 3. Registrar y verificar alertas
        for ap, carga in carga_aps.items():
            self.historial_carga[ap].append(round(carga, 2))
            if carga > self.capacidad_ap * 0.8:
                self.alertas.append(f"⚠️ AP{ap+1} al {round((carga/self.capacidad_ap)*100)}% de capacidad en paso {paso+1}")
            if carga > self.capacidad_ap:
                self.alertas.append(f"🚨 AP{ap+1} SATURADO en paso {paso+1}")

        return carga_aps

    def ejecutar_simulacion(self):
        """Ejecuta la simulación completa"""
        for paso in range(self.tiempo_simulacion):
            self.simular_paso(paso)

        # Calcular estadísticas finales
        estadisticas = {}
        for ap, historial in self.historial_carga.items():
            if historial:
                promedio = sum(historial) / len(historial)
                maximo = max(historial)
                estadisticas[ap] = {
                    'promedio': round(promedio, 2),
                    'maximo': round(maximo, 2),
                    'historial': historial
                }

        return {
            'estadisticas': estadisticas,
            'alertas': self.alertas,
            'tiempo_simulacion': self.tiempo_simulacion,
            'num_aps': self.num_aps,
            'capacidad_ap': self.capacidad_ap,
            'num_usuarios': self.num_usuarios
        }

def simular_trafico(escenario):
    """Función wrapper para el endpoint"""
    simulador = SimuladorRedes(escenario)
    return simulador.ejecutar_simulacion()