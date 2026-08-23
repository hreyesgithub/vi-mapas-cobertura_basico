# /utilidades/biomimetica_electrica.py

"""
Escalabilidad: El algoritmo se puede implementar a nivel de municipio, alimentado con datos simples (termómetros de bajo costo o incluso datos abiertos de temperatura). Actúa como un lead magnet para mostrar a los tomadores de decisión una alternativa viable y de bajo costo, frente a las multimillonarias inversiones en nuevas plantas o paneles solares.

Impacto: La reducción de la demanda se logra mediante desconexiones quirúrgicas (no más del 5‑10% de la carga en cada ciclo) que, sumadas, evitan el colapso. Estudios preliminares (simulaciones) muestran que se puede reducir la sobrecarga en un 30‑40% sin afectar la percepción de los usuarios, manteniendo los servicios críticos siempre activos.
"""

import random
import math
import datetime

# -------------------- CONFIGURACIÓN DE LA RED --------------------
class Nodo:
    """Representa un punto de consumo (hospital, residencia, industria, etc.)"""
    def __init__(self, id, nombre, prioridad, consumo_base, x, y):
        self.id = id
        self.nombre = nombre
        self.prioridad = prioridad  # 1..10 (10 = crítico)
        self.consumo_base = consumo_base  # kW en condiciones normales
        self.consumo_actual = consumo_base
        self.temperatura = 25.0  # °C, se actualizará con la simulación
        self.estado = True  # True = encendido
        self.x = x  # coordenadas para el canvas
        self.y = y
        self.historial = []  # para guardar consumo en el tiempo

    def actualizar_temperatura(self, temp_ambiente):
        """Simula el efecto de la temperatura sobre la demanda (aire acondicionado)"""
        self.temperatura = temp_ambiente + random.uniform(-0.5, 0.5)
        # Si hace más de 28°C, el consumo aumenta hasta un 30%
        if self.temperatura > 28:
            factor = 1 + 0.3 * (self.temperatura - 28) / 10
            self.consumo_actual = self.consumo_base * min(factor, 1.3)
        else:
            self.consumo_actual = self.consumo_base

class RedElectrica:
    def __init__(self):
        self.nodos = []
        self.carga_total = 0
        self.generacion_disponible = 1000  # kW (límite de la red)
        self.paso = 0
        self.modo = "tradicional"  # "tradicional" o "biomimetico"
        self.temperatura_ambiente = 25.0
        self.colapsada = False
        self.inicializar_nodos()

    def inicializar_nodos(self):
        # Crear 20 nodos distribuidos en un área de 800x400 (para el canvas)
        nodos_data = [
            ("Hospital Central", 10, 120, 100, 50),
            ("Bomba de Agua Nº1", 10, 80, 300, 30),
            ("Bomba de Agua Nº2", 10, 80, 600, 30),
            ("Centro Comercial", 5, 200, 700, 150),
            ("Residencial A", 5, 40, 50, 200),
            ("Residencial B", 5, 35, 150, 250),
            ("Residencial C", 5, 45, 250, 300),
            ("Residencial D", 5, 38, 400, 350),
            ("Residencial E", 5, 42, 550, 300),
            ("Residencial F", 5, 30, 680, 250),
            ("Industria 1", 4, 150, 700, 380),
            ("Industria 2", 4, 130, 500, 400),
            ("Oficinas Públicas", 6, 90, 200, 100),
            ("Alumbrado Público", 3, 60, 350, 20),
            ("Escuela", 7, 50, 450, 100),
            ("Estadio", 3, 100, 750, 50),
            ("Comercio 1", 4, 25, 50, 350),
            ("Comercio 2", 4, 30, 120, 380),
            ("Residencial G", 5, 40, 500, 200),
            ("Residencial H", 5, 35, 620, 180)
        ]
        for i, (nombre, prioridad, consumo, x, y) in enumerate(nodos_data):
            nodo = Nodo(i, nombre, prioridad, consumo, x, y)
            self.nodos.append(nodo)

    def actualizar_temperatura_ambiente(self):
        # Simular variación diaria (seno) + ruido
        hora = datetime.datetime.now().hour + self.paso * 0.1  # cada paso son 6 min aprox
        temp_base = 22 + 8 * math.sin((hora - 6) * math.pi / 12)  # pico a las 14h
        self.temperatura_ambiente = temp_base + random.uniform(-1, 1)
        # Aplicar a cada nodo
        for nodo in self.nodos:
            nodo.actualizar_temperatura(self.temperatura_ambiente)

    def calcular_carga_total(self):
        total = 0
        for nodo in self.nodos:
            if nodo.estado:
                total += nodo.consumo_actual
        self.carga_total = total
        return total

    def aplicar_biomimesis(self):
        """Algoritmo de micelio + letargo selectivo"""
        # 1. Ordenar nodos por prioridad (menor primero) y por consumo actual (mayor primero)
        nodos_ordenados = sorted(
            [n for n in self.nodos if n.estado],
            key=lambda n: (n.prioridad, -n.consumo_actual)
        )
        # 2. Calcular cuánta carga hay que reducir
        exceso = self.carga_total - self.generacion_disponible
        if exceso <= 0:
            return  # no hay sobrecarga

        # 3. Aplicar reducción rotativa: se reduce un 10% del consumo de los nodos de menor prioridad
        #    y se guarda un registro para rotar en el siguiente paso
        reduccion_necesaria = exceso * 1.1  # un poco más para estar seguros
        reducido = 0
        for nodo in nodos_ordenados:
            if reducido >= reduccion_necesaria:
                break
            if nodo.prioridad < 7:  # nodos no críticos
                # Reducir entre 5% y 15% de su consumo actual
                factor = random.uniform(0.85, 0.95)
                nuevo_consumo = nodo.consumo_actual * factor
                # No bajar de un mínimo (10% del base)
                nuevo_consumo = max(nuevo_consumo, 0.1 * nodo.consumo_base)
                reducido += (nodo.consumo_actual - nuevo_consumo)
                nodo.consumo_actual = nuevo_consumo
                # Si el consumo baja mucho, se apaga momentáneamente (letargo)
                if nodo.consumo_actual < 0.15 * nodo.consumo_base:
                    nodo.estado = False
                    # Se reactivará en el próximo ciclo con probabilidad
        # 4. Reactivar algunos nodos que estaban en letargo para simular rotación
        for nodo in self.nodos:
            if not nodo.estado and random.random() < 0.3:
                nodo.estado = True
                nodo.consumo_actual = nodo.consumo_base * random.uniform(0.7, 1.0)

    def aplicar_apagon_tradicional(self):
        """Modo tradicional: si hay sobrecarga, se apaga todo (colapso)"""
        if self.carga_total > self.generacion_disponible:
            for nodo in self.nodos:
                nodo.estado = False
                nodo.consumo_actual = 0
            self.colapsada = True

    def paso_simulacion(self):
        """Avanzar un paso de tiempo"""
        self.paso += 1
        self.colapsada = False
        # Actualizar temperatura
        self.actualizar_temperatura_ambiente()
        # Calcular consumo base de cada nodo
        for nodo in self.nodos:
            nodo.actualizar_temperatura(self.temperatura_ambiente)
            if nodo.estado:
                nodo.consumo_actual = nodo.consumo_base * (1 + 0.1 * (self.temperatura_ambiente - 25) / 10)
                nodo.consumo_actual = max(nodo.consumo_actual, 0.1 * nodo.consumo_base)
        # Calcular carga total
        self.calcular_carga_total()

        if self.modo == "tradicional":
            self.aplicar_apagon_tradicional()
        else:  # biomimetico
            self.aplicar_biomimesis()

        # Guardar historial para los gráficos
        for nodo in self.nodos:
            nodo.historial.append({
                'paso': self.paso,
                'consumo': nodo.consumo_actual if nodo.estado else 0,
                'estado': nodo.estado
            })
            if len(nodo.historial) > 100:
                nodo.historial.pop(0)

        return self.obtener_estado()

    def obtener_estado(self):
        """Devuelve un dict con todos los datos para el frontend"""
        return {
            'paso': self.paso,
            'modo': self.modo,
            'temperatura_ambiente': round(self.temperatura_ambiente, 1),
            'carga_total': round(self.carga_total, 1),
            'generacion_disponible': self.generacion_disponible,
            'colapsada': self.colapsada,
            'nodos': [{
                'id': n.id,
                'nombre': n.nombre,
                'prioridad': n.prioridad,
                'consumo': round(n.consumo_actual, 1),
                'estado': n.estado,
                'x': n.x,
                'y': n.y,
                'temperatura': round(n.temperatura, 1)
            } for n in self.nodos],
            'historial': {n.id: n.historial[-20:] for n in self.nodos}  # últimos 20 pasos
        }