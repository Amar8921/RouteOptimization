from ortools.constraint_solver import pywrapcp, routing_enums_pb2

def optimize_routes(distance_matrix, demands, capacities, time_windows, travel_times, depot_idx=0):
    """
    Solves the Capacitated Vehicle Routing Problem with Time Windows (CVRPTW).
    """
    # Create the routing index manager.
    manager = pywrapcp.RoutingIndexManager(
        len(distance_matrix), len(capacities), depot_idx)

    # Create Routing Model.
    routing = pywrapcp.RoutingModel(manager)

    # 1. Distance Callback (for costs)
    def distance_callback(from_index, to_index):
        from_node = manager.IndexToNode(from_index)
        to_node = manager.IndexToNode(to_index)
        return int(distance_matrix[from_node][to_node] * 1000)

    transit_callback_index = routing.RegisterTransitCallback(distance_callback)
    routing.SetArcCostEvaluatorOfAllVehicles(transit_callback_index)

    # 2. Demand Callback (for capacity)
    def demand_callback(from_index):
        from_node = manager.IndexToNode(from_index)
        return demands[from_node]

    demand_callback_index = routing.RegisterUnaryTransitCallback(demand_callback)
    routing.AddDimensionWithVehicleCapacity(
        demand_callback_index,
        0,  # null capacity slack
        capacities,  # vehicle maximum capacities
        True,  # start cumul to zero
        "Capacity",
    )

    # 3. Time Callback (for time windows)
    def time_callback(from_index, to_index):
        """Returns the travel time between the two nodes."""
        from_node = manager.IndexToNode(from_index)
        to_node = manager.IndexToNode(to_index)
        return int(travel_times[from_node][to_node])

    time_callback_index = routing.RegisterTransitCallback(time_callback)
    routing.AddDimension(
        time_callback_index,
        30,  # allow waiting time (slack) up to 30 mins
        240, # maximum time per vehicle (4 hours)
        False, # Don't force start cumul to zero (allows flexible start)
        "Time",
    )
    time_dimension = routing.GetDimensionOrDie("Time")

    # Add time window constraints for each location except depot.
    for location_idx, time_window in enumerate(time_windows):
        if location_idx == depot_idx:
            continue
        index = manager.NodeToIndex(location_idx)
        time_dimension.CumulVar(index).SetRange(time_window[0], time_window[1])

    # Add time window constraints for each vehicle start and end at depot.
    for vehicle_id in range(len(capacities)):
        index = routing.Start(vehicle_id)
        time_dimension.CumulVar(index).SetRange(time_windows[depot_idx][0], time_windows[depot_idx][1])
        
    for vehicle_id in range(len(capacities)):
        index = routing.End(vehicle_id)
        time_dimension.CumulVar(index).SetRange(time_windows[depot_idx][0], time_windows[depot_idx][1])

    # Instantiate route start and end times to produce feasible times.
    for i in range(len(capacities)):
        routing.AddVariableMaximizedByFinalizer(
            time_dimension.CumulVar(routing.Start(i)))
        routing.AddVariableMinimizedByFinalizer(
            time_dimension.CumulVar(routing.End(i)))

    # Setting search parameters.
    search_parameters = pywrapcp.DefaultRoutingSearchParameters()
    search_parameters.first_solution_strategy = (
        routing_enums_pb2.FirstSolutionStrategy.PATH_CHEAPEST_ARC)
    search_parameters.local_search_metaheuristic = (
        routing_enums_pb2.LocalSearchMetaheuristic.GUIDED_LOCAL_SEARCH)
    search_parameters.time_limit.seconds = 5

    # Solve the problem.
    solution = routing.SolveWithParameters(search_parameters)

    # Extract routes from the solution
    routes = []
    if solution:
        for vehicle_id in range(len(capacities)):
            index = routing.Start(vehicle_id)
            plan_output = []
            route_distance = 0
            while not routing.IsEnd(index):
                node_index = manager.IndexToNode(index)
                time_var = time_dimension.CumulVar(index)
                plan_output.append({
                    "node": node_index,
                    "arrival_time": solution.Min(time_var),
                    "cumulative_distance": route_distance / 1000.0 # Convert to km
                })
                previous_index = index
                index = solution.Value(routing.NextVar(index))
                route_distance += routing.GetArcCostForVehicle(previous_index, index, vehicle_id)
            
            node_index = manager.IndexToNode(index)
            time_var = time_dimension.CumulVar(index)
            plan_output.append({
                "node": node_index,
                "arrival_time": solution.Min(time_var),
                "cumulative_distance": route_distance / 1000.0 # Convert to km
            })
            
            if len(plan_output) > 2:
                routes.append({
                    "vehicle_id": vehicle_id,
                    "route": plan_output,
                    "distance_meters": route_distance
                })
    
    return routes
