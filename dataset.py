import os
import torch
import numpy as np
from torch.utils.data import Dataset
import torch.nn.functional as F
import networkx as nx


# def generate_good_layouts(adj_matrix, num_variations=10):
#     """
#     Generates graph layouts using different algorithms and perturbations that aim to evenly 
#     distribute nodes and minimize edge intersections.

#     Args:
#     - adj_matrix (np.array): The adjacency matrix of the input graph.
#     - num_variations (int): Number of layout variations to generate.

#     Returns:
#     - layouts (list of np.array): List of generated graph layouts.
#     """
#     G = nx.from_numpy_array(adj_matrix)
#     layouts = []

#     # Advanced layout algorithms
#     spring_layout = nx.spring_layout(G)  # Force-directed layout (evenly distributes nodes)
#     kamada_kawai_layout = nx.kamada_kawai_layout(G)  # Minimizes edge lengths and crossings
#     fruchterman_reingold_layout = nx.fruchterman_reingold_layout(G)  # Balanced force-directed layout

#     # Append these high-quality layouts
#     layouts.append(np.array([spring_layout[node] for node in G.nodes()]))
#     layouts.append(np.array([kamada_kawai_layout[node] for node in G.nodes()]))
#     layouts.append(np.array([fruchterman_reingold_layout[node] for node in G.nodes()]))

#     # Repulsion mechanism to adjust nodes that are too close
#     def apply_repulsion(layout, repulsion_distance=0.1):
#         for i in range(len(layout)):
#             for j in range(i + 1, len(layout)):
#                 dist = np.linalg.norm(layout[i] - layout[j])
#                 if dist < repulsion_distance:  # If nodes are too close
#                     # Apply a small random displacement to move nodes apart
#                     direction = (layout[i] - layout[j]) / (dist + 1e-6)  # Normalize
#                     layout[i] += direction * 0.05  # Push node i
#                     layout[j] -= direction * 0.05  # Push node j in the opposite direction
#         return layout

#     # Generate additional variations with small perturbations and repulsion applied
#     for _ in range(num_variations):
#         perturbed_layout = np.array([spring_layout[node] + np.random.normal(scale=0.05, size=2) for node in G.nodes()])
#         perturbed_layout = apply_repulsion(perturbed_layout)  # Apply repulsion to improve node spacing
#         layouts.append(perturbed_layout)

#     return layouts

# #circular layout
# def generate_good_layouts(adj_matrix, num_variations=10):
#     """
#     Generates graph layouts focusing on circular layouts with perturbations for variety,
#     with added post-processing to minimize edge crossings.
    
#     Args:
#     - adj_matrix (np.array): The adjacency matrix of the input graph.
#     - num_variations (int): Number of layout variations to generate.
    
#     Returns:
#     - layouts (list of np.array): List of generated graph layouts.
#     """
#     G = nx.from_numpy_array(adj_matrix)
#     layouts = []

#     # Circular Layout (primary focus)
#     circular_layout = nx.circular_layout(G)
#     layouts.append(np.array([circular_layout[node] for node in G.nodes()]))

#     # Repulsion mechanism to adjust nodes that are too close
#     def apply_repulsion(layout, repulsion_distance=0.1):
#         for i in range(len(layout)):
#             for j in range(i + 1, len(layout)):
#                 dist = np.linalg.norm(layout[i] - layout[j])
#                 if dist < repulsion_distance:  # If nodes are too close
#                     # Apply a small random displacement to move nodes apart
#                     direction = (layout[i] - layout[j]) / (dist + 1e-6)  # Normalize
#                     force = 0.05 / (dist + 1e-6)  # Stronger repulsion as nodes get closer
#                     layout[i] += direction * force  # Push node i
#                     layout[j] -= direction * force  # Push node j in the opposite direction
#         return layout

#     # Edge crossing minimization using spring layout post-optimization
#     def minimize_edge_crossings(layout, G):
#         pos = {i: layout[i] for i in range(len(layout))}
#         spring_layout = nx.spring_layout(G, pos=pos, iterations=100)
#         kamada_kawai_layout = nx.kamada_kawai_layout(G, pos=spring_layout)  # Refine using Kamada-Kawai
#         return np.array([kamada_kawai_layout[node] for node in G.nodes()])
#         #return np.array([spring_layout[node] for node in G.nodes()])

#     # Generate additional variations by adding small perturbations to the circular layout
#     for _ in range(num_variations):
#         perturbed_layout = np.array([circular_layout[node] + np.random.normal(scale=0.005, size=2) for node in G.nodes()])
#         perturbed_layout = apply_repulsion(perturbed_layout)  # Apply repulsion to improve node spacing
#         optimized_layout = minimize_edge_crossings(perturbed_layout, G)  # Further optimize layout to reduce edge crossings
#         layouts.append(optimized_layout)

#     return layouts


def generate_good_layouts(adj_matrix, num_variations=10, layout_type='circular'):
    """
    Generates graph layouts focusing on various layout types for ablation experiments.
    Supports circular, shell, random, and spiral layouts, with perturbations for variety.
    
    Args:
    - adj_matrix (np.array): The adjacency matrix of the input graph.
    - num_variations (int): Number of layout variations to generate.
    - layout_type (str): The type of layout to generate ('circular', 'shell', 'random', 'spiral').
    
    Returns:
    - layouts (list of np.array): List of generated graph layouts.
    """
    G = nx.from_numpy_array(adj_matrix)
    layouts = []

    # Define primary layout based on the layout_type
    if layout_type == 'circular':
        primary_layout = nx.circular_layout(G)
    elif layout_type == 'shell':
        primary_layout = nx.shell_layout(G)
    elif layout_type == 'random':
        primary_layout = nx.random_layout(G)
    elif layout_type == 'spiral':
        # Create a custom spiral layout function
        def spiral_layout(G):
            n = len(G.nodes())
            spiral_layout_positions = {}
            for i, node in enumerate(G.nodes()):
                angle = i * 0.1
                radius = 0.1 * i
                spiral_layout_positions[node] = np.array([radius * np.cos(angle), radius * np.sin(angle)])
            return spiral_layout_positions
        
        primary_layout = spiral_layout(G)
    else:
        raise ValueError("Invalid layout_type. Choose from 'circular', 'shell', 'random', or 'spiral'.")

    # Convert layout to numpy array format
    layouts.append(np.array([primary_layout[node] for node in G.nodes()]))

    # Repulsion mechanism to adjust nodes that are too close
    def apply_repulsion(layout, repulsion_distance=0.1):
        for i in range(len(layout)):
            for j in range(i + 1, len(layout)):
                dist = np.linalg.norm(layout[i] - layout[j])
                if dist < repulsion_distance:
                    direction = (layout[i] - layout[j]) / (dist + 1e-6)
                    force = 0.05 / (dist + 1e-6)
                    layout[i] += direction * force
                    layout[j] -= direction * force
        return layout

    # Edge crossing minimization using spring layout post-optimization
    def minimize_edge_crossings(layout, G):
        pos = {i: layout[i] for i in range(len(layout))}
        spring_layout = nx.spring_layout(G, pos=pos, iterations=100)
        kamada_kawai_layout = nx.kamada_kawai_layout(G, pos=spring_layout)
        return np.array([kamada_kawai_layout[node] for node in G.nodes()])

    # Generate additional variations by adding small perturbations to the primary layout
    for _ in range(num_variations):
        perturbed_layout = np.array([primary_layout[node] + np.random.normal(scale=0.005, size=2) for node in G.nodes()])
        perturbed_layout = apply_repulsion(perturbed_layout)
        optimized_layout = minimize_edge_crossings(perturbed_layout, G)
        # optimized_layout = perturbed_layout
        layouts.append(optimized_layout)

    return layouts


# ---- Custom Dataset for Hamiltonian and Non-Hamiltonian Graph Layouts ----
class HamiltonianGraphDataset(Dataset):
    def __init__(self, hamiltonian_dir, non_hamiltonian_dir, pretrain=False):
        # Load all .npy files from both directories
        self.hamiltonian_files = [os.path.join(hamiltonian_dir, f) for f in os.listdir(hamiltonian_dir) if f.endswith('.npy')]
        self.non_hamiltonian_files = [os.path.join(non_hamiltonian_dir, f) for f in os.listdir(non_hamiltonian_dir) if f.endswith('.npy')]
        
        # Assign labels: 1 for Hamiltonian, 0 for non-Hamiltonian
        self.labels = [1] * len(self.hamiltonian_files) + [0] * len(self.non_hamiltonian_files)
        
        # Combine the file lists
        self.all_files = self.hamiltonian_files + self.non_hamiltonian_files

        self.pretrain = pretrain

    def __len__(self):
        return len(self.all_files)


    def __getitem__(self, idx):
        # Load the adjacency matrix from the .npy file
        adj_matrix = np.load(self.all_files[idx])  # Now, it's just an adjacency matrix

        # Optionally, you can generate random node coordinates for each graph
        num_nodes = adj_matrix.shape[0]

        #node_coords = np.random.rand(num_nodes, 2) * 2 - 1  # Random node coordinates in [-1, 1] range

        if self.pretrain:
            # Generate various good layouts for pretraining
            layouts = generate_good_layouts(adj_matrix)
            node_coords = layouts[np.random.randint(len(layouts))]  # Choose one randomly
        else:
            # Random node coordinates for each graph (for normal training)
            #node_coords = np.random.rand(num_nodes, 2) * 2 - 1  # Random node coordinates in [-1, 1] range
            layouts = generate_good_layouts(adj_matrix) # Also use good layouts for training
            node_coords = layouts[np.random.randint(len(layouts))]  # Choose one randomly
        
        # Get the corresponding label
        label = self.labels[idx]
        return node_coords, adj_matrix, label

def custom_collate(batch, device):
    # Extract node coordinates and adjacency matrices
    node_coords = [torch.tensor(each[0], dtype=torch.float32).to(device) for each in batch]
    adj_matrices = [torch.tensor(each[1], dtype=torch.float32).to(device) for each in batch]

    # Determine the maximum size among the adjacency matrices
    max_size = max(matrix.size(0) for matrix in adj_matrices)

    # Pad each adjacency matrix and node coordinate matrix to the maximum size
    padded_coords = [F.pad(coord, (0, 0, 0, max_size - coord.size(0))) for coord in node_coords]
    padded_adj_matrices = [F.pad(matrix, (0, max_size - matrix.size(0), 0, max_size - matrix.size(1))) for matrix in adj_matrices]

    # Create batched tensors for both padded coordinates and adjacency matrices
    coords_batch = torch.stack(padded_coords)
    adj_matrices_batch = torch.stack(padded_adj_matrices)

    labels = torch.tensor([each[2] for each in batch], dtype=torch.long).to(device)  # Labels

    return coords_batch, adj_matrices_batch, labels
