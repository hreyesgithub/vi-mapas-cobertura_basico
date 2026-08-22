# utilidades/turnos_optimizer.py
import random
from collections import defaultdict
from enum import Enum
from datetime import date, timedelta
import calendar

class DiaSemana(Enum):
    LUNES = 0
    MARTES = 1
    MIERCOLES = 2
    JUEVES = 3
    VIERNES = 4
    SABADO = 5
    DOMINGO = 6

class TipoTurno(Enum):
    LUNES_VIERNES = "Lunes a Viernes"
    SABADO = "Sábado"
    DOMINGO = "Domingo"
    SEMANA_COMPLETA = "Semana Completa (Guardia)"

BLOQUES_HORARIOS = ["07:00-15:00", "15:00-23:00", "23:00-07:00"]

def obtener_dias_por_tipo(tipo_turno):
    if tipo_turno == TipoTurno.LUNES_VIERNES:
        return [DiaSemana.LUNES, DiaSemana.MARTES, DiaSemana.MIERCOLES,
                DiaSemana.JUEVES, DiaSemana.VIERNES]
    elif tipo_turno == TipoTurno.SABADO:
        return [DiaSemana.SABADO]
    elif tipo_turno == TipoTurno.DOMINGO:
        return [DiaSemana.DOMINGO]
    elif tipo_turno == TipoTurno.SEMANA_COMPLETA:
        return list(DiaSemana)

def obtener_dias_mes(anio, mes):
    num_dias = calendar.monthrange(anio, mes)[1]
    dias_mes = []
    for dia in range(1, num_dias + 1):
        fecha = date(anio, mes, dia)
        dia_semana = DiaSemana(fecha.weekday())
        dias_mes.append((fecha, dia_semana))
    return dias_mes

def calcular_costo_total(horario, empleados):
    costo_total = 0
    for tipo_turno, bloques in horario.items():
        dias = obtener_dias_por_tipo(TipoTurno(tipo_turno))
        for bloque, empleados_nombres in bloques.items():
            for emp_nombre in empleados_nombres:
                empleado = next(e for e in empleados if e["nombre"] == emp_nombre)
                horas_trabajadas = 8 * len(dias) # type:ignore
                costo_total += empleado["coste"] * horas_trabajadas
    return costo_total

def crear_horario_aleatorio(empleados, requerimientos):
    horario = defaultdict(lambda: defaultdict(list))
    for tipo_turno, req_turno in requerimientos.items():
        dias = obtener_dias_por_tipo(TipoTurno(tipo_turno))
        for bloque, req in req_turno.items():
            empleados_disponibles = [
                e["nombre"] for e in empleados
                if any(e["disponibilidad"].get(dia.name) for dia in dias) # type:ignore
                and bloque in e["preferencias"]
            ]
            if empleados_disponibles and req > 0:
                num_asignar = max(1, min(req, len(empleados_disponibles)))
                horario[tipo_turno][bloque] = random.sample(empleados_disponibles, num_asignar)
    return dict(horario)

def evaluar_horario(horario, empleados, requerimientos):
    puntuacion = 0
    horas_por_empleado = defaultdict(int)
    dias_trabajados_por_empleado = defaultdict(set)

    # Cobertura
    for tipo_turno, req_turno in requerimientos.items():
        dias = obtener_dias_por_tipo(TipoTurno(tipo_turno))
        for bloque, req in req_turno.items():
            asignados = len(horario.get(tipo_turno, {}).get(bloque, []))
            if asignados > 0:
                puntuacion += 100 * asignados * len(dias) # type:ignore
            if asignados >= req:
                puntuacion += 1000 * len(dias) # type:ignore
    # Costos y horas
    for tipo_turno, bloques in horario.items():
        dias = obtener_dias_por_tipo(TipoTurno(tipo_turno))
        for bloque, emp_nombres in bloques.items():
            for emp_nombre in emp_nombres:
                empleado = next(e for e in empleados if e["nombre"] == emp_nombre)
                horas = 8 * len(dias) # type:ignore
                horas_por_empleado[emp_nombre] += horas
                puntuacion -= empleado["coste"] * horas
                for dia in dias: # type:ignore
                    dias_trabajados_por_empleado[emp_nombre].add(dia)

    # Penalizaciones
    for emp_nombre, horas in horas_por_empleado.items():
        empleado = next(e for e in empleados if e["nombre"] == emp_nombre)
        if horas > empleado["max_horas"]:
            puntuacion -= 50 * (horas - empleado["max_horas"])

    for emp_nombre, dias_trab in dias_trabajados_por_empleado.items():
        empleado = next(e for e in empleados if e["nombre"] == emp_nombre)
        for dia in dias_trab:
            if not empleado["disponibilidad"].get(dia.name):
                puntuacion -= 200

    # Bonificaciones por preferencias
    for tipo_turno, bloques in horario.items():
        for bloque, emp_nombres in bloques.items():
            for emp_nombre in emp_nombres:
                empleado = next(e for e in empleados if e["nombre"] == emp_nombre)
                if bloque in empleado["preferencias"]:
                    puntuacion += 20 * len(obtener_dias_por_tipo(TipoTurno(tipo_turno))) # type:ignore

    return puntuacion

def seleccionar(poblacion_con_puntuacion):
    participantes = random.sample(poblacion_con_puntuacion, 3)
    return max(participantes, key=lambda x: x[1])[0]

def cruzar(horario1, horario2):
    nuevo_horario = defaultdict(lambda: defaultdict(list))
    for tipo_turno in list(horario1.keys()) | list(horario2.keys()): # type:ignore
        for bloque in BLOQUES_HORARIOS:
            if random.random() < 0.5:
                nuevo_horario[tipo_turno][bloque] = horario1.get(tipo_turno, {}).get(bloque, [])[:]
            else:
                nuevo_horario[tipo_turno][bloque] = horario2.get(tipo_turno, {}).get(bloque, [])[:]
    return dict(nuevo_horario)

def mutar(horario, empleados, requerimientos):
    if not horario:
        return horario
    tipo_turno_a_mutar = random.choice(list(requerimientos.keys()))
    bloque_a_mutar = random.choice(BLOQUES_HORARIOS)
    accion = random.choice(["añadir", "eliminar", "reemplazar"])
    
    dias = obtener_dias_por_tipo(TipoTurno(tipo_turno_a_mutar))
    empleados_disponibles = [
        e["nombre"] for e in empleados
        if all(e["disponibilidad"].get(dia.name) for dia in dias) # type:ignore
        and bloque_a_mutar in e["preferencias"]
    ]
    if not empleados_disponibles:
        return horario

    if accion == "añadir":
        if len(horario.get(tipo_turno_a_mutar, {}).get(bloque_a_mutar, [])) < len(empleados_disponibles):
            nuevo_emp = random.choice([e for e in empleados_disponibles 
                                      if e not in horario.get(tipo_turno_a_mutar, {}).get(bloque_a_mutar, [])])
            if tipo_turno_a_mutar not in horario:
                horario[tipo_turno_a_mutar] = {}
            if bloque_a_mutar not in horario[tipo_turno_a_mutar]:
                horario[tipo_turno_a_mutar][bloque_a_mutar] = []
            horario[tipo_turno_a_mutar][bloque_a_mutar].append(nuevo_emp)
    elif accion == "eliminar" and horario.get(tipo_turno_a_mutar, {}).get(bloque_a_mutar, []):
        horario[tipo_turno_a_mutar][bloque_a_mutar].pop(random.randint(0, len(horario[tipo_turno_a_mutar][bloque_a_mutar])-1))
    elif accion == "reemplazar" and horario.get(tipo_turno_a_mutar, {}).get(bloque_a_mutar, []):
        idx = random.randint(0, len(horario[tipo_turno_a_mutar][bloque_a_mutar])-1)
        posibles = [e for e in empleados_disponibles if e != horario[tipo_turno_a_mutar][bloque_a_mutar][idx]]
        if posibles:
            horario[tipo_turno_a_mutar][bloque_a_mutar][idx] = random.choice(posibles)
    return horario

def ejecutar_algoritmo_genetico(empleados, requerimientos, tamano_poblacion=30, generaciones=50, tasa_mutacion=0.3):
    poblacion = [crear_horario_aleatorio(empleados, requerimientos) for _ in range(tamano_poblacion)]
    
    for _ in range(generaciones):
        poblacion_con_puntuacion = [(h, evaluar_horario(h, empleados, requerimientos)) for h in poblacion]
        mejor_horario, mejor_puntuacion = max(poblacion_con_puntuacion, key=lambda x: x[1])
        
        nueva_poblacion = [mejor_horario]
        while len(nueva_poblacion) < tamano_poblacion:
            padre1 = seleccionar(poblacion_con_puntuacion)
            padre2 = seleccionar(poblacion_con_puntuacion)
            hijo = cruzar(padre1, padre2)
            if random.random() < tasa_mutacion:
                hijo = mutar(hijo, empleados, requerimientos)
            nueva_poblacion.append(hijo)
        poblacion = nueva_poblacion
    
    mejor_final = max([(h, evaluar_horario(h, empleados, requerimientos)) for h in poblacion], key=lambda x: x[1])
    return mejor_final[0], calcular_costo_total(mejor_final[0], empleados)